import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from rag_local.daemon.client import (
    daemon_healthcheck,
    daemon_shutdown,
)
from rag_local.daemon.port_file import read_port_file


def _start_detached_daemon(parent_pid: int | None = None, port: int = 0) -> bool:
    """Inicia el Worker Daemon desacoplado en segundo plano de manera 100% invisible."""
    executable = sys.executable
    if sys.platform == "win32":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            executable = str(pythonw)

    cmd = [
        executable,
        "-m",
        "rag_local.daemon",
        "--port",
        str(port),
    ]
    if parent_pid is not None:
        cmd.extend(["--parent-pid", str(parent_pid)])

    flags = 0
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE (Oculta cualquier ventana de consola)
        flags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
        )

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    subprocess.Popen(  # noqa: S603
        cmd,
        startupinfo=startupinfo,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env=env,
    )

    # Esperar a que el daemon complete la carga y warm-up
    from rag_local.core import config

    timeout = config.DAEMON_STARTUP_TIMEOUT
    start_time = time.time()
    while time.time() - start_time < timeout:
        health = daemon_healthcheck()
        if health is not None and health.get("status") == "ok":
            return True
        time.sleep(0.3)

    return False


def run_daemon_cli(args: argparse.Namespace) -> int:
    """Ejecuta los comandos del CLI rag-daemon."""
    command = args.command or "status"

    from rag_local.core import config
    from rag_local.daemon.port_file import get_port_file_path

    port_path = get_port_file_path()

    if command == "status":
        health = daemon_healthcheck()
        if health:
            dev = health.get("device", "cpu")
            port = health.get("port")
            pid = health.get("pid")
            uptime_s = float(health.get("uptime_s", 0))
            mins = int(uptime_s) // 60
            secs = int(uptime_s) % 60
            uptime_str = f"{mins:02d}:{secs:02d}"
            vram = health.get("vram")

            vram_str = "N/A (CPU)"
            if isinstance(vram, dict) and "used_mb" in vram:
                vram_str = f"{vram['used_mb']} MB / {vram.get('total_mb', 0)} MB"

            sys.stdout.write(
                f"WORKER_DAEMON: Activo\n"
                f"  - Archivo de estado: {port_path}\n"
                f"  - Puerto: {port}\n"
                f"  - PID: {pid}\n"
                f"  - Dispositivo: {dev}\n"
                f"  - VRAM: {vram_str}\n"
                f"  - Tiempo Activo: {uptime_str}\n"
            )
            return 0
        else:
            sys.stdout.write(
                f"WORKER_DAEMON: Inactivo (Modo bajo demanda)\n"
                f"  - Directorio global: {config.DAEMON_DATA_DIR}\n"
            )
            return 0

    elif command == "start":
        existing = daemon_healthcheck()
        if existing:
            sys.stdout.write(
                f"[DAEMON] El Worker Daemon ya está activo en el puerto "
                f"{existing.get('port')} (PID {existing.get('pid')}, "
                f"Dispositivo: {existing.get('device')}).\n"
            )
            return 0

        sys.stdout.write(
            "[DAEMON] Iniciando Worker Daemon y precargando modelos en VRAM...\n"
        )
        parent_pid = args.parent_pid if args.parent_pid is not None else os.getpid()
        success = _start_detached_daemon(parent_pid=parent_pid, port=args.port)

        if success:
            health = daemon_healthcheck()
            dev = health.get("device", "cpu") if health else "desconocido"
            port = health.get("port") if health else "?"
            sys.stdout.write(
                f"[DAEMON] Worker Daemon iniciado con éxito en http://127.0.0.1:{port} "
                f"({dev.upper()}).\n"
            )
            return 0
        else:
            sys.stderr.write(
                f"[AVISO] El Worker Daemon no respondió en "
                f"{int(config.DAEMON_STARTUP_TIMEOUT)}s. "
                "Puede seguir inicializándose en segundo plano "
                "(primer inicio frío). "
                "Ejecute 'rag-daemon status' para verificar.\n"
            )
            return 0

    elif command == "stop":
        port_data = read_port_file()
        if not port_data:
            sys.stdout.write("[DAEMON] No hay ningún Worker Daemon activo.\n")
            return 0

        sys.stdout.write(
            "[DAEMON] Deteniendo Worker Daemon y liberando memoria VRAM...\n"
        )
        daemon_shutdown()

        # Esperar confirmación de detención
        for _ in range(15):
            time.sleep(0.2)
            if not daemon_healthcheck():
                break

        sys.stdout.write(
            "[DAEMON] Worker Daemon detenido. Memoria VRAM liberada a 0 MB.\n"
        )
        return 0

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gestor del Worker Daemon para RAG Local"
    )
    subparsers = parser.add_subparsers(dest="command", help="Comando a ejecutar")

    # subcomando start
    parser_start = subparsers.add_parser(
        "start", help="Inicia el Worker Daemon en segundo plano"
    )
    parser_start.add_argument(
        "--parent-pid", type=int, default=None, help="PID padre a monitorear"
    )
    parser_start.add_argument(
        "--port", type=int, default=0, help="Puerto TCP (0 para dinámico)"
    )

    # subcomando stop
    subparsers.add_parser("stop", help="Detiene el Worker Daemon y libera VRAM")

    # subcomando status
    subparsers.add_parser("status", help="Muestra el estado actual del Worker Daemon")

    args = parser.parse_args()
    code = run_daemon_cli(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
