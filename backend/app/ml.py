from __future__ import annotations

import multiprocessing
import queue
import threading
from datetime import datetime

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from .database import Database
from .models import (
    ClusterAssignment,
    ClusterRun,
    WalletFeatureSnapshot,
    utcnow,
)
from .repositories import log_event


class ClusterRequest(BaseModel):
    algorithm: str = "kmeans"
    reducer: str = "pca"
    feature_version: str = "wallet_features.v3"
    feature_names: list[str] = []
    n_clusters: int = Field(default=3, ge=2, le=50)
    min_cluster_size: int = Field(default=5, ge=2, le=1000)
    min_samples: int | None = Field(default=None, ge=1, le=1000)
    random_state: int = 42
    umap_neighbors: int = Field(default=15, ge=2, le=200)
    umap_min_dist: float = Field(default=0.1, ge=0.0, le=1.0)
    umap_metric: str = "cosine"
    reducer_components: int = Field(default=5, ge=2, le=20)
    scaler: str = "robust"
    winsorize: bool = True
    winsor_lower: float = Field(default=0.01, ge=0.0, le=0.25)
    winsor_upper: float = Field(default=0.99, ge=0.75, le=1.0)
    log_transform: bool = False
    cluster_selection_method: str = "eom"

    @model_validator(mode="after")
    def validate_choices(self):
        if self.algorithm not in {"kmeans", "hdbscan"}:
            raise ValueError("Алгоритм должен быть kmeans или hdbscan")
        if self.reducer not in {"pca", "umap"}:
            raise ValueError("Метод снижения размерности должен быть pca или umap")
        if self.scaler not in {"robust", "standard"}:
            raise ValueError("Масштабирование должно быть robust или standard")
        if self.umap_metric not in {"cosine", "euclidean", "manhattan"}:
            raise ValueError("Неподдерживаемая метрика UMAP")
        if self.cluster_selection_method not in {"eom", "leaf"}:
            raise ValueError("cluster_selection_method должен быть eom или leaf")
        if self.winsor_lower >= self.winsor_upper:
            raise ValueError("winsor_lower должен быть меньше winsor_upper")
        return self


def _ml_worker(payload: dict, result_queue) -> None:
    try:
        import numpy as np
        from sklearn.cluster import KMeans
        from sklearn.decomposition import PCA
        from sklearn.impute import SimpleImputer
        from sklearn.metrics import silhouette_score
        from sklearn.preprocessing import RobustScaler, StandardScaler

        matrix = np.asarray(payload["matrix"], dtype=float)
        matrix = SimpleImputer(strategy="median").fit_transform(matrix)
        if payload.get("winsorize", True):
            lower = np.quantile(matrix, payload.get("winsor_lower", 0.01), axis=0)
            upper = np.quantile(matrix, payload.get("winsor_upper", 0.99), axis=0)
            matrix = np.clip(matrix, lower, upper)
        if payload.get("log_transform", False):
            matrix = np.sign(matrix) * np.log1p(np.abs(matrix))
        scaler = (
            RobustScaler()
            if payload.get("scaler", "robust") == "robust"
            else StandardScaler()
        )
        scaled = scaler.fit_transform(matrix)
        reducer_name = payload["reducer"]
        component_count = min(
            payload.get("reducer_components", 5),
            scaled.shape[1],
            max(2, scaled.shape[0] - 1),
        )
        if reducer_name == "umap":
            try:
                import umap
            except ImportError as exc:
                raise RuntimeError("UMAP is not installed") from exc
            reducer = umap.UMAP(
                n_components=component_count,
                n_neighbors=min(payload["umap_neighbors"], len(matrix) - 1),
                min_dist=payload.get("umap_min_dist", 0.1),
                metric=payload.get("umap_metric", "cosine"),
                random_state=payload["random_state"],
            )
            cluster_matrix = reducer.fit_transform(scaled)
            if component_count == 2:
                coordinates = cluster_matrix
            else:
                visualization = umap.UMAP(
                    n_components=2,
                    n_neighbors=min(payload["umap_neighbors"], len(matrix) - 1),
                    min_dist=payload.get("umap_min_dist", 0.1),
                    metric=payload.get("umap_metric", "cosine"),
                    random_state=payload["random_state"],
                )
                coordinates = visualization.fit_transform(scaled)
        else:
            reducer = PCA(n_components=component_count)
            cluster_matrix = reducer.fit_transform(scaled)
            coordinates = cluster_matrix[:, :2]
        if coordinates.shape[1] == 1:
            coordinates = np.column_stack([coordinates[:, 0], np.zeros(len(matrix))])

        if payload["algorithm"] == "hdbscan":
            try:
                import hdbscan
            except ImportError as exc:
                raise RuntimeError("HDBSCAN is not installed") from exc
            model = hdbscan.HDBSCAN(
                min_cluster_size=payload["min_cluster_size"],
                min_samples=payload["min_samples"],
                cluster_selection_method=payload.get(
                    "cluster_selection_method", "eom"
                ),
                prediction_data=True,
            )
            labels = model.fit_predict(cluster_matrix)
            probabilities = model.probabilities_.tolist()
        else:
            if payload["n_clusters"] >= len(matrix):
                raise ValueError("n_clusters должен быть меньше числа образцов")
            model = KMeans(
                n_clusters=payload["n_clusters"],
                random_state=payload["random_state"],
                n_init="auto",
            )
            labels = model.fit_predict(cluster_matrix)
            probabilities = [None] * len(labels)

        unique_clusters = sorted({int(label) for label in labels if int(label) >= 0})
        metrics = {
            "samples": len(matrix),
            "cluster_count": len(unique_clusters),
            "noise_count": sum(int(label) < 0 for label in labels),
            "reducer_components": component_count,
        }
        if payload["algorithm"] == "kmeans":
            metrics["inertia"] = float(model.inertia_)
        non_noise = np.asarray([int(label) >= 0 for label in labels])
        non_noise_labels = labels[non_noise]
        if (
            non_noise.sum() >= 3
            and len(set(int(label) for label in non_noise_labels)) >= 2
            and len(set(int(label) for label in non_noise_labels)) < non_noise.sum()
        ):
            metrics["silhouette"] = float(
                silhouette_score(cluster_matrix[non_noise], non_noise_labels)
            )

        profiles = {}
        overall_mean = np.mean(matrix, axis=0)
        for label in sorted({int(item) for item in labels}):
            mask = labels == label
            cluster_mean = np.mean(matrix[mask], axis=0)
            profiles[str(label)] = {
                "size": int(mask.sum()),
                "means": {
                    name: float(cluster_mean[index])
                    for index, name in enumerate(payload["feature_names"])
                },
                "medians": {
                    name: float(np.median(matrix[mask, index]))
                    for index, name in enumerate(payload["feature_names"])
                },
                "mean_delta_from_all": {
                    name: float(cluster_mean[index] - overall_mean[index])
                    for index, name in enumerate(payload["feature_names"])
                },
            }
        result_queue.put(
            {
                "ok": True,
                "labels": [int(item) for item in labels],
                "probabilities": probabilities,
                "coordinates": coordinates.tolist(),
                "metrics": metrics,
                "profiles": profiles,
            }
        )
    except Exception as exc:
        result_queue.put(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        )


class MLManager:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._processes: dict[int, multiprocessing.Process] = {}

    def start(self, request: ClusterRequest) -> ClusterRun:
        wallet_ids, feature_names, matrix = self._load_matrix(request)
        run = ClusterRun(
            state="queued",
            stage="preparing",
            progress_percent=5,
            algorithm=request.algorithm,
            reducer=request.reducer,
            feature_version=request.feature_version,
            parameters=request.model_dump(exclude={"feature_names", "feature_version"}),
            feature_names=feature_names,
        )
        with self.database.session() as session:
            session.add(run)
            session.flush()
            run_id = run.id
            log_event(
                session,
                "info",
                "cluster.started",
                f"Starting {request.algorithm} for {len(wallet_ids)} wallets",
                cluster_run_id=run_id,
            )
        payload = {
            **request.model_dump(),
            "wallet_ids": wallet_ids,
            "feature_names": feature_names,
            "matrix": matrix,
        }
        result_queue = self._context.Queue(maxsize=1)
        process = self._context.Process(
            target=_ml_worker,
            args=(payload, result_queue),
            name=f"wallet-cluster-{run_id}",
        )
        process.start()
        with self.database.session() as session:
            stored = session.get(ClusterRun, run_id)
            stored.state = "running"
            stored.stage = "reducing_and_clustering"
            stored.progress_percent = 15
            stored.started_at = utcnow()
        with self._lock:
            self._processes[run_id] = process
        threading.Thread(
            target=self._monitor,
            args=(run_id, wallet_ids, process, result_queue),
            daemon=True,
            name=f"cluster-monitor-{run_id}",
        ).start()
        with self.database.session() as session:
            return session.get(ClusterRun, run_id)

    def _load_matrix(
        self, request: ClusterRequest
    ) -> tuple[list[int], list[str], list[list[float | None]]]:
        with self.database.session() as session:
            latest = (
                select(
                    WalletFeatureSnapshot.wallet_id,
                    func.max(WalletFeatureSnapshot.created_at).label("latest"),
                )
                .where(WalletFeatureSnapshot.version == request.feature_version)
                .group_by(WalletFeatureSnapshot.wallet_id)
                .subquery()
            )
            snapshots = list(
                session.scalars(
                    select(WalletFeatureSnapshot).join(
                        latest,
                        (WalletFeatureSnapshot.wallet_id == latest.c.wallet_id)
                        & (WalletFeatureSnapshot.created_at == latest.c.latest),
                    )
                ).all()
            )
        if len(snapshots) < 3:
            raise ValueError("Требуется не менее трёх снимков признаков")
        if request.feature_names:
            names = request.feature_names
        else:
            common = set(snapshots[0].features)
            for snapshot in snapshots[1:]:
                common &= set(snapshot.features)
            names = sorted(
                name
                for name in common
                if all(
                    isinstance(snapshot.features.get(name), (int, float))
                    and not isinstance(snapshot.features.get(name), bool)
                    for snapshot in snapshots
                )
            )
        if not names:
            raise ValueError("Нет доступных числовых признаков")
        unknown = [
            name
            for name in names
            if any(not isinstance(snapshot.features.get(name), (int, float)) for snapshot in snapshots)
        ]
        if unknown:
            raise ValueError(f"Нечисловые или отсутствующие признаки: {', '.join(unknown)}")
        return (
            [snapshot.wallet_id for snapshot in snapshots],
            names,
            [
                [float(snapshot.features[name]) for name in names]
                for snapshot in snapshots
            ],
        )

    def _monitor(
        self,
        run_id: int,
        wallet_ids: list[int],
        process: multiprocessing.Process,
        result_queue,
    ) -> None:
        process.join()
        with self._lock:
            self._processes.pop(run_id, None)
        with self.database.session() as session:
            run = session.get(ClusterRun, run_id)
            if run is None or run.cancel_requested:
                if run:
                    run.state = "cancelled"
                    run.stage = "cancelled"
                    run.finished_at = utcnow()
                return
        try:
            result = result_queue.get(timeout=2)
        except queue.Empty:
            result = {
                "ok": False,
                "error": f"Child process exited with code {process.exitcode}",
            }
        with self.database.session() as session:
            run = session.get(ClusterRun, run_id)
            if result.get("ok"):
                for index, wallet_id in enumerate(wallet_ids):
                    coordinates = result["coordinates"][index]
                    session.add(
                        ClusterAssignment(
                            cluster_run_id=run_id,
                            wallet_id=wallet_id,
                            cluster_label=result["labels"][index],
                            probability=result["probabilities"][index],
                            x=float(coordinates[0]),
                            y=float(coordinates[1]),
                        )
                    )
                run.metrics = result["metrics"]
                run.profiles = result["profiles"]
                run.state = "completed"
                run.stage = "completed"
                run.progress_percent = 100
                event = "cluster.completed"
                message = "Clustering completed"
                level = "info"
            else:
                run.state = "failed"
                run.stage = "failed"
                run.error = result.get("error", "Unknown child process error")
                event = "cluster.failed"
                message = run.error
                level = "error"
            run.finished_at = utcnow()
            log_event(
                session,
                level,
                event,
                message,
                cluster_run_id=run_id,
            )

    def cancel(self, run_id: int) -> ClusterRun:
        with self.database.session() as session:
            run = session.get(ClusterRun, run_id)
            if run is None:
                raise ValueError("Запуск кластеризации не найден")
            if run.state not in {"queued", "running"}:
                return run
            run.cancel_requested = True
            run.state = "cancelling"
            run.stage = "stopping"
        with self._lock:
            process = self._processes.get(run_id)
        if process and process.is_alive():
            process.terminate()
        with self.database.session() as session:
            run = session.get(ClusterRun, run_id)
            run.state = "cancelled"
            run.stage = "cancelled"
            run.finished_at = utcnow()
            log_event(
                session,
                "info",
                "cluster.cancelled",
                "Cluster run cancelled",
                cluster_run_id=run_id,
            )
            return run

    def shutdown(self) -> None:
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            if process.is_alive():
                process.terminate()
