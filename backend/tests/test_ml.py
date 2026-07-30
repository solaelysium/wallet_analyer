from __future__ import annotations

from queue import Queue
import time

from sqlalchemy import func, select

from app.config import Settings
from app.database import Database
from app.ml import ClusterRequest, MLManager, _ml_worker
from app.models import Chain, ClusterAssignment, ClusterRun, Wallet, WalletFeatureSnapshot


def test_cluster_parameters_are_validated() -> None:
    request = ClusterRequest(algorithm="kmeans", reducer="pca", n_clusters=2)
    assert request.n_clusters == 2
    assert request.feature_version == "wallet_features.v2"


def test_kmeans_worker_returns_coordinates_and_metrics() -> None:
    output = Queue()
    payload = {
        "matrix": [
            [0.0, 0.0],
            [0.1, 0.2],
            [0.2, 0.1],
            [10.0, 10.0],
            [10.1, 10.2],
            [10.2, 10.1],
        ],
        "feature_names": ["activity", "volume"],
        "algorithm": "kmeans",
        "reducer": "pca",
        "n_clusters": 2,
        "min_cluster_size": 2,
        "min_samples": None,
        "random_state": 42,
        "umap_neighbors": 3,
    }
    _ml_worker(payload, output)
    result = output.get_nowait()
    assert result["ok"] is True
    assert len(result["coordinates"]) == 6
    assert result["metrics"]["cluster_count"] == 2
    assert result["metrics"]["inertia"] >= 0
    assert sum(profile["size"] for profile in result["profiles"].values()) == 6
    assert all("medians" in profile for profile in result["profiles"].values())


def test_ml_manager_uses_spawned_process_and_persists_results(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'ml.db').as_posix()}",
        app_secret_key="test",
        infura_api_keys=[],
        etherscan_api_keys=[],
        coingecko_api_keys=[],
    )
    database = Database(settings)
    database.initialize()
    with database.session() as session:
        chain = session.scalar(select(Chain).where(Chain.slug == "ethereum"))
        for index in range(6):
            wallet = Wallet(
                chain_id=chain.id,
                address=f"0x{index + 1:040x}",
            )
            session.add(wallet)
            session.flush()
            session.add(
                WalletFeatureSnapshot(
                    wallet_id=wallet.id,
                        version="wallet_features.v2",
                    as_of_block=1,
                    features={
                        "activity": float(index // 3 * 10 + index % 3 / 10),
                        "volume": float(index // 3 * 10 + index % 3 / 5),
                    },
                )
            )
    manager = MLManager(database)
    run = manager.start(
        ClusterRequest(
            algorithm="kmeans",
            reducer="pca",
            n_clusters=2,
            feature_names=["activity", "volume"],
        )
    )
    deadline = time.monotonic() + 15
    state = run.state
    while time.monotonic() < deadline and state in {"queued", "running"}:
        time.sleep(0.05)
        with database.session() as session:
            state = session.get(ClusterRun, run.id).state
    with database.session() as session:
        stored = session.get(ClusterRun, run.id)
        assignment_count = session.scalar(
            select(func.count(ClusterAssignment.id)).where(
                ClusterAssignment.cluster_run_id == run.id
            )
        )
        assert stored.state == "completed", stored.error
        assert stored.stage == "completed"
        assert stored.progress_percent == 100
        assert assignment_count == 6
    manager.shutdown()
