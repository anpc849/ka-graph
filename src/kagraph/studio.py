import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

from kagraph._studio_config import (
    DEFAULT_BACKEND_PORT,
    DEFAULT_BACKEND_URL,
    DEFAULT_DB_PATH,
    DEFAULT_DB_URL,
    DEFAULT_FRONTEND_PORT,
    DEFAULT_STUDIO_HOST,
    STUDIO_RUNTIME_PATH,
    write_studio_runtime,
)


def is_tool_installed(name):
    return shutil.which(name) is not None


def port_is_open(port, host=DEFAULT_STUDIO_HOST):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def wait_for_port(port, timeout=120, host=DEFAULT_STUDIO_HOST):
    start = time.time()
    while time.time() - start < timeout:
        if port_is_open(port, host):
            return True
        time.sleep(1)
    return False


def print_tail(filename, lines=15):
    try:
        with open(filename, "r", encoding="utf-8", errors="replace") as f:
            content = f.readlines()
            print(f"\n--- Last {lines} lines of {filename} ---")
            for line in content[-lines:]:
                print(line.rstrip())
            print("-" * 40)
    except Exception as e:
        print(f"Could not read {filename}: {e}")


def follow_file(filename):
    print(f"\n[VERBOSE] Streaming backend log from {filename}. Press Ctrl+C to stop streaming.\n")
    try:
        with open(filename, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.25)
    except KeyboardInterrupt:
        print("\n[VERBOSE] Stopped backend log streaming. Studio processes remain in the background.")
    except Exception as e:
        print(f"[VERBOSE] Could not stream backend log: {e}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Start KaTrace Studio backend and frontend.")
    parser.add_argument(
        "--mode",
        choices=["local", "localtunnel"],
        default=os.getenv("KAGRAPH_STUDIO_MODE", "localtunnel"),
        help="Use 'local' for 127.0.0.1 only, or 'localtunnel' to expose the frontend publicly.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Keep this process attached and stream backend.log in real time.",
    )
    parser.add_argument(
        "--frontend-mode",
        choices=["auto", "dev", "production"],
        default=os.getenv("KAGRAPH_STUDIO_FRONTEND_MODE", "auto"),
        help="Use production frontend for stable tunnel chunks, dev frontend for local iteration, or auto.",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    print("=== KaTrace Studio Starter ===")
    print(f"Mode: {args.mode}")
    print(f"Verbose backend log streaming: {args.verbose}")
    frontend_mode = args.frontend_mode
    if frontend_mode == "auto":
        frontend_mode = "production" if args.mode == "localtunnel" else "dev"
    print(f"Frontend mode: {frontend_mode}")

    in_use_ports = [
        port
        for port in (DEFAULT_BACKEND_PORT, DEFAULT_FRONTEND_PORT)
        if port_is_open(port)
    ]
    if in_use_ports:
        print(
            f"Warning: port(s) {in_use_ports} already appear to be in use. "
            "Studio will still start; if startup fails, check backend.log and frontend.log."
        )

    print("Checking system dependencies...")
    if not is_tool_installed("npm"):
        print("npm not found. Installing nodejs and npm...")
        subprocess.run(["apt-get", "update"], capture_output=True)
        subprocess.run(["apt-get", "install", "-y", "nodejs", "npm"], capture_output=True)
    else:
        print("npm is already installed.")

    if args.mode == "localtunnel" and not is_tool_installed("lt"):
        print("localtunnel not found. Installing globally via npm...")
        subprocess.run(["npm", "install", "-g", "localtunnel"], capture_output=True)
    elif args.mode == "localtunnel":
        print("localtunnel is already installed.")

    root_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(root_dir, "webapp", "backend")
    frontend_dir = os.path.join(root_dir, "webapp", "frontend")
    frontend_next_cache = os.path.join(frontend_dir, ".next")
    if os.path.isdir(frontend_next_cache):
        print("Clearing stale Next.js cache...")
        shutil.rmtree(frontend_next_cache, ignore_errors=True)

    backend_log_path = os.path.abspath("backend.log")
    frontend_log_path = os.path.abspath("frontend.log")
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    backend_env = os.environ.copy()
    backend_env["PYTHONUNBUFFERED"] = "1"
    backend_env["KATRACE_BACKEND_LOG"] = backend_log_path
    backend_env["KATRACE_DB_URL"] = DEFAULT_DB_URL
    write_studio_runtime(
        status="starting",
        mode=args.mode,
        frontend_mode=frontend_mode,
        verbose=args.verbose,
        backend_log=backend_log_path,
        frontend_log=frontend_log_path,
        database_path=str(DEFAULT_DB_PATH),
        database_url=DEFAULT_DB_URL,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    print(f"Starting FastAPI backend on port {DEFAULT_BACKEND_PORT}...")
    backend_log = open(backend_log_path, "w", encoding="utf-8")
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            DEFAULT_STUDIO_HOST,
            "--port",
            str(DEFAULT_BACKEND_PORT),
        ],
        cwd=backend_dir,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=backend_env,
    )

    if args.mode == "localtunnel":
        print(f"Starting Next.js frontend on port {DEFAULT_FRONTEND_PORT} and opening LocalTunnel...")
    else:
        print(f"Starting Next.js frontend on port {DEFAULT_FRONTEND_PORT} for local access...")
    frontend_log = open(frontend_log_path, "w", encoding="utf-8")
    frontend_command = "npm install --no-audit --no-fund"
    if frontend_mode == "production":
        frontend_command += " && npm run build"
        frontend_command += f" && npx next start -H {DEFAULT_STUDIO_HOST} -p {DEFAULT_FRONTEND_PORT}"
    else:
        frontend_command += f" && npx next dev -H {DEFAULT_STUDIO_HOST} -p {DEFAULT_FRONTEND_PORT}"
    if args.mode == "localtunnel":
        frontend_command = f"({frontend_command}) & lt --port {DEFAULT_FRONTEND_PORT} --local-host {DEFAULT_STUDIO_HOST}"
    subprocess.Popen(
        frontend_command,
        shell=True,
        cwd=frontend_dir,
        stdout=frontend_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    print("Waiting for servers to boot (this may take up to 2 minutes)...")
    backend_ready = wait_for_port(DEFAULT_BACKEND_PORT)
    frontend_ready = wait_for_port(DEFAULT_FRONTEND_PORT)
    if backend_ready and frontend_ready:
        if args.mode == "localtunnel":
            time.sleep(8)
        try:
            password = None
            if args.mode == "localtunnel":
                password = urllib.request.urlopen("https://loca.lt/mytunnelpassword").read().decode("utf8")
            print("\n" + "=" * 60)
            print("KaTrace Studio is running in the background.")
            if password:
                print(f"Your LocalTunnel Password is: {password}")
            print(f"Backend API URL for tracing: {DEFAULT_BACKEND_URL}")
            print(f"SQLite database file: {DEFAULT_DB_PATH}")
            print(f"Runtime config file: {STUDIO_RUNTIME_PATH}")
            print(f"Backend log file: {backend_log_path}")

            url = None
            if args.mode == "localtunnel":
                with open(frontend_log_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if "your url is:" in line:
                            url = line.split("your url is:")[1].strip()

            if url:
                print(f"Your Public URL: {url}")
                write_studio_runtime(
                    status="running",
                    mode=args.mode,
                    frontend_mode=frontend_mode,
                    verbose=args.verbose,
                    public_url=url,
                    local_url=f"http://{DEFAULT_STUDIO_HOST}:{DEFAULT_FRONTEND_PORT}",
                    backend_log=backend_log_path,
                    frontend_log=frontend_log_path,
                    database_path=str(DEFAULT_DB_PATH),
                    database_url=DEFAULT_DB_URL,
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
            elif args.mode == "localtunnel":
                print("Check 'frontend.log' for your public 'loca.lt' URL.")
                write_studio_runtime(
                    status="running",
                    mode=args.mode,
                    frontend_mode=frontend_mode,
                    verbose=args.verbose,
                    backend_log=backend_log_path,
                    frontend_log=frontend_log_path,
                    database_path=str(DEFAULT_DB_PATH),
                    database_url=DEFAULT_DB_URL,
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                local_url = f"http://{DEFAULT_STUDIO_HOST}:{DEFAULT_FRONTEND_PORT}"
                print(f"Your Local URL: {local_url}")
                write_studio_runtime(
                    status="running",
                    mode=args.mode,
                    frontend_mode=frontend_mode,
                    verbose=args.verbose,
                    local_url=local_url,
                    backend_log=backend_log_path,
                    frontend_log=frontend_log_path,
                    database_path=str(DEFAULT_DB_PATH),
                    database_url=DEFAULT_DB_URL,
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
            print("=" * 60 + "\n")

        except Exception as e:
            print(f"Could not automatically fetch LocalTunnel password: {e}")
            write_studio_runtime(
                status="running",
                mode=args.mode,
                frontend_mode=frontend_mode,
                verbose=args.verbose,
                backend_log=backend_log_path,
                frontend_log=frontend_log_path,
                database_path=str(DEFAULT_DB_PATH),
                database_url=DEFAULT_DB_URL,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
    else:
        print("Warning: Servers took too long to boot or failed.")
        print(f"Backend ready: {backend_ready}; frontend ready: {frontend_ready}")
        write_studio_runtime(
            status="boot_timeout",
            mode=args.mode,
            frontend_mode=frontend_mode,
            verbose=args.verbose,
            backend_ready=backend_ready,
            frontend_ready=frontend_ready,
            backend_log=backend_log_path,
            frontend_log=frontend_log_path,
            database_path=str(DEFAULT_DB_PATH),
            database_url=DEFAULT_DB_URL,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    print("\n[DEBUG] Server Logs for Bad Gateway Troubleshooting:")
    print_tail(backend_log_path, 15)
    print_tail(frontend_log_path, 25)
    if args.verbose and args.mode != "localtunnel":
        follow_file(backend_log_path)
    elif args.verbose:
        print("[VERBOSE] Real-time backend log streaming is disabled in localtunnel mode so notebook cells do not block.")


if __name__ == "__main__":
    main()
