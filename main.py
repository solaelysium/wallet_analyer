import argparse
import asyncio
import csv
import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from source.settings import CONFIGS, Logger, set_log_prefix

logger = Logger(level="INFO", output_dir="outputs\\logs", output_filename="logs.log")

from source import DataHandler, WebClient

CSV_LOCK = Lock()
PROCESSED_LOCK = Lock()
PROCESSED_COUNT = 0
TOTAL_ADDRESSES = 0

# Get configurations
CHAIN_NAME = CONFIGS.CRYPTO.ETHEREUM_CHAIN_NAME  #! you must check it appropriately for your contract
NETWORK = CONFIGS.CRYPTO.ETHEREUM_NETWORK  #! you must check it appropriately for your contract
RPC_URL = CONFIGS.CRYPTO.ETHEREUM_MAINNET  #! you must check it appropriately for your contract
CSV_PATH = os.path.join(CONFIGS.OUTPUT_DIR, f"{CHAIN_NAME}.csv")
API_DELAY = float(os.getenv("API_DELAY", "0.5"))  # seconds
MAX_TX_PER_WALLET = int(os.getenv("MAX_TX_PER_WALLET", "3000"))  # max count of transactions per wallet
MIN_TX_PER_WALLET = int(os.getenv("MIN_TX_PER_WALLET", "15"))  # min count of transactions per wallet


def parse_args():
    """
    Parse arguments
    """
    parser = argparse.ArgumentParser(description="Wallet analyzer")
    parser.add_argument("--workers", type=int, default=3, help="Workers per Infura key (default: 3)")
    # Python 3.9+ supports BooleanOptionalAction
    parser.add_argument("--get_unique", action=argparse.BooleanOptionalAction, default=False, help="Extract unique addresses before run")
    return parser.parse_args()


def get_and_save_unique_addresses() -> list[str]:
    """
    Get and save unique addresses from file

    Returns:
        list[str]: List of addresses
    """
    with open("address_list.txt", "r", encoding="utf-8") as f:
        addresses = [line.strip() for line in f]

    with open("unique_addresses.txt", "w", encoding="utf-8") as f:
        for address in set(addresses):
            f.write(address + "\n")


def read_unique_addresses() -> list[str]:
    """
    Read addresses from file

    Returns:
        list[str]: List of addresses

    Example:
    >>> addresses = read_unique_addresses()
    >>> print(addresses)
    ['0x171D1285a9a8De3f16d4c45706d4E2F4A5C9e175', '0x5bdf85216ec1e38D6458C870992A69e38e03F7Ef']
    """

    with open("unique_addresses.txt", "r") as f:
        addresses = [line.strip() for line in f]

    return addresses


def append_row_to_csv(path: str, row: dict, fieldnames: list[str]):
    """
    Append a single row to outputs/stats.csv using csv

    Args:
        path (str): Path to the csv file
        row (dict): Row to append
        fieldnames (list[str]): Fieldnames to use
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with CSV_LOCK:
        file_exists = os.path.exists(path)
        with open(path, "a+", newline="", encoding="utf-8") as csv_file:
            # Check for ending
            csv_file.seek(0, os.SEEK_END)
            if csv_file.tell() > 0:  # file is not empty
                csv_file.seek(csv_file.tell() - 1)
                if csv_file.read(1) != "\n":
                    csv_file.write("\n")

            writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
            if not file_exists:
                # если файла не было — нужно перемотать в начало и записать заголовок
                csv_file.seek(0)
                writer.writeheader()
            writer.writerow(row)
            # ensure flush to disk
            csv_file.flush()
            os.fsync(csv_file.fileno())


def worker_process_addresses(addresses: list[str], t_idx: int, w_idx: int, web_client: WebClient):
    global PROCESSED_COUNT, TOTAL_ADDRESSES
    # Per-worker log prefix
    set_log_prefix(f"[T{t_idx}][W{w_idx}]")
    data_handler = DataHandler(web_client=web_client)

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)

        for address in addresses:
            logger.info(f"Processing | {address}")

            stage = "init"
            try:
                # * Checking for contract
                is_wallet = web_client.is_wallet(address)

                if is_wallet is None:
                    logger.error(f"Error getting is_wallet for address {address}")
                    continue
                if not is_wallet:
                    logger.warning(f"Wallet {address} is a contract address")
                    continue

                # * Get transactions info (async wrapper over block-range, asc order)
                stage = "fetch_erc20_txs"
                ERC20_TXS = loop.run_until_complete(
                    web_client.get_erc20_txs_by_block_range_async(
                        wallet_address=address,
                        sort="asc",
                        timeout_sec=float(os.getenv("ETHERSCAN_TIMEOUT", "60")),
                    )
                )

                logger.info("ERC20 transactions fetched")
                stage = "fetch_internal_txs"
                internal_txs = loop.run_until_complete(
                    web_client.get_internal_txs_by_block_range_async(
                        wallet_address=address,
                        sort="asc",
                        timeout_sec=float(os.getenv("ETHERSCAN_TIMEOUT", "60")),
                    )
                )
                logger.info("Internal transactions fetched")

                # * Get balance of wallet
                stage = "get_balance"
                balance = web_client.get_balance(address)

                logger.info("Balance fetched")

                # * Length of ERC20_TXS
                logger.info(f"ERC20 transactions length: {len(ERC20_TXS)}")

                stage = "length_guards"
                if len(ERC20_TXS) < MIN_TX_PER_WALLET:
                    logger.warning(f"ERC20 transactions length is less than {MIN_TX_PER_WALLET}")
                    continue

                if len(ERC20_TXS) > MAX_TX_PER_WALLET:
                    logger.warning(f"ERC20 transactions length is greater than {MAX_TX_PER_WALLET}")
                    continue

                # * Analyze data
                stage = "analyze_stats"
                stats = data_handler.get_stats(
                    wallet_address=address,
                    wallet_balance=balance,
                    ERC20_TXS=ERC20_TXS,
                    internal_txs=internal_txs,
                )

                if stats is None:
                    logger.warning("Stats not fetched")
                    continue

                # * Append to CSV
                stage = "write_csv"
                append_row_to_csv(path=CSV_PATH, row=stats, fieldnames=stats.keys())
                logger.warning("Done")

            except Exception as e:
                err_name = type(e).__name__
                tb = traceback.format_exc()
                logger.warning(f"Failed at {stage}: {err_name}: {e}\n{tb}")
                continue
            finally:
                with PROCESSED_LOCK:
                    PROCESSED_COUNT += 1
                    cur = PROCESSED_COUNT
                    total = TOTAL_ADDRESSES
                logger.info(f"Addresses processed {cur}/{total}")
    finally:
        loop.close()


def key_thread_runner(t_idx: int, addresses: list[str], rpc_url: str, etherscan_key: str, workers_per_key: int):
    # Per-key prefix for logs in this thread
    set_log_prefix(f"[T{t_idx}]")
    logger.info(f"Starting key thread | RPC: {rpc_url}")

    # One client per key thread
    web_client = WebClient(rpc_url, NETWORK, ETHERSCAN_API_KEY=etherscan_key)

    # Split addresses for workers
    worker_chunks = split_into_chunks(addresses, max(1, workers_per_key))
    if not worker_chunks:
        logger.info("No addresses assigned to this key thread")
        return

    with ThreadPoolExecutor(max_workers=len(worker_chunks)) as wpool:
        futures = []
        for w_idx, chunk in enumerate(worker_chunks, start=1):
            futures.append(wpool.submit(worker_process_addresses, chunk, t_idx, w_idx, web_client))
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                logger.error(f"Worker failed: {e}")
    logger.info("Key thread finished")


def split_into_chunks(lst: list[str], num_chunks: int) -> list[list[str]]:
    if num_chunks <= 0:
        num_chunks = 1
    chunk_size = (len(lst) + num_chunks - 1) // num_chunks  # ceil division
    if chunk_size == 0:
        return []
    return [lst[i : i + chunk_size] for i in range(0, len(lst), chunk_size)]


# Main function
def main(workers_per_key: int = 3):
    # Read addresses from file
    addresses = read_unique_addresses()
    logger.info(f"Unique addresses: {len(addresses)}")
    global TOTAL_ADDRESSES
    TOTAL_ADDRESSES = len(addresses)

    # Prepare per-key threads (one per Infura key)
    infura_keys = CONFIGS.CRYPTO.INFURA_API_KEYS
    rpc_urls = [f"https://mainnet.infura.io/v3/{k}" for k in infura_keys]
    etherscan_keys = CONFIGS.CRYPTO.ETHERSCAN_API_KEYS or [""]

    num_keys = len(rpc_urls)
    # Split addresses across key-threads
    key_chunks = split_into_chunks(addresses, num_keys)
    logger.info(
        f"Infura keys: {num_keys}; Etherscan keys: {len(etherscan_keys)}; "
        f"Workers per key: {max(1, int(workers_per_key))}; Key chunk sizes: {[len(c) for c in key_chunks]}"
    )

    # Run one top-level thread per key; inside it spawn workers_per_key workers
    with ThreadPoolExecutor(max_workers=num_keys) as tpool:
        futures = []
        for t_idx, (rpc, chunk) in enumerate(zip(rpc_urls, key_chunks), start=1):
            es_key = etherscan_keys[(t_idx - 1) % len(etherscan_keys)] if etherscan_keys else ""
            futures.append(tpool.submit(key_thread_runner, t_idx, chunk, rpc, es_key, int(workers_per_key)))
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                logger.error(f"Key thread failed: {e}")

    logger.info("All addresses processed")


if __name__ == "__main__":
    args = parse_args()
    if args.get_unique:
        get_and_save_unique_addresses()
    main(args.workers)


# CLI Comand:
# python main.py --workers 3 --get_unique
# python main.py --workers 3 --no-get_unique
