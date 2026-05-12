#!/usr/bin/env python3
"""Small TCP forwarder for exposing a sandboxed demo port on the Spark host."""

from __future__ import annotations

import argparse
import select
import socket
import threading


def relay(left: socket.socket, right: socket.socket) -> None:
    sockets = [left, right]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 30)
            if not readable:
                continue
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                target = right if source is left else left
                target.sendall(data)
    finally:
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass


def serve(bind_host: str, bind_port: int, target_host: str, target_port: int) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind_host, bind_port))
    listener.listen(128)
    print(f"forwarding {bind_host}:{bind_port} -> {target_host}:{target_port}", flush=True)
    while True:
        client, _ = listener.accept()
        upstream = socket.create_connection((target_host, target_port), timeout=10)
        thread = threading.Thread(target=relay, args=(client, upstream), daemon=True)
        thread.start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Expose a sandbox TCP port on the host.")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--bind-port", type=int, required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, required=True)
    args = parser.parse_args()
    serve(args.bind_host, args.bind_port, args.target_host, args.target_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
