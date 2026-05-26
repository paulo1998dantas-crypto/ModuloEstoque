import socket
import time
import urllib.request
import webbrowser
import os
from threading import Thread

from waitress import serve

from app import app
from config import BASE_DIR
from services.excel_service import create_template_files


APP_NAME = "Estoque J.I Montadora"
HOST = "127.0.0.1"
START_PORT = 5000


def find_port(start_port=START_PORT):
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((HOST, port)) != 0:
                return port
    raise RuntimeError("Nao foi possivel encontrar uma porta local livre.")


def wait_until_ready(url, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def run_server(port):
    serve(app, host=HOST, port=port, threads=8)


def open_desktop_window(url):
    try:
        import webview

        webview.create_window(
            APP_NAME,
            url,
            width=1280,
            height=820,
            min_size=(1040, 680),
            text_select=True,
        )
        webview.start()
        return True
    except Exception:
        webbrowser.open(url)
        return False


def main():
    create_template_files(BASE_DIR)
    port = find_port()
    url = f"http://{HOST}:{port}/"
    thread = Thread(target=run_server, args=(port,), daemon=True)
    thread.start()

    if not wait_until_ready(url):
        raise RuntimeError("O servidor local nao iniciou corretamente.")

    if os.environ.get("ESTOQUE_HEADLESS_TEST") == "1":
        while True:
            time.sleep(60)

    opened_native_window = open_desktop_window(url)
    if not opened_native_window:
        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()
