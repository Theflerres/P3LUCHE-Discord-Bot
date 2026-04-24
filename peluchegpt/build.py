"""Build unificado do PelucheGPT (backend + frontend)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
DIST_DIR = ROOT / "dist" / "PelucheGPT"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    """Executa comando e interrompe build em caso de erro."""
    print(f"-> Executando: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_ollama() -> None:
    """Valida se o binário do Ollama está disponível no sistema."""
    if shutil.which("ollama"):
        print("Ollama encontrado.")
        return
    print("Ollama não encontrado. Abrindo página de download...")
    webbrowser.open("https://ollama.com/download")
    raise SystemExit("Instale o Ollama e execute o build novamente.")


def install_python_dependencies() -> None:
    """Instala dependências do backend usando o Python atual."""
    run([sys.executable, "-m", "pip", "install", "-r", str(BACKEND_DIR / "requirements.txt")])


def package_backend() -> Path:
    """Empacota backend FastAPI em um único backend.exe."""
    spec_path = BACKEND_DIR / "main.py"
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--name",
            "backend",
            str(spec_path),
        ],
        cwd=BACKEND_DIR,
    )
    return BACKEND_DIR / "dist" / "backend.exe"


def package_frontend() -> None:
    """Gera executável Tauri da interface."""
    npm = "npm.cmd" if os.name == "nt" else "npm"
    run([npm, "install"], cwd=FRONTEND_DIR)
    run([npm, "run", "tauri", "build"], cwd=FRONTEND_DIR)


def assemble_dist(backend_exe: Path) -> None:
    """Agrupa artefatos finais em dist/PelucheGPT."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    target_backend = DIST_DIR / "backend.exe"
    shutil.copy2(backend_exe, target_backend)
    print(f"Backend copiado para {target_backend}")

    # Copia build do Tauri quando disponível.
    tauri_bundle = FRONTEND_DIR / "src-tauri" / "target" / "release"
    if tauri_bundle.exists():
        for file in tauri_bundle.glob("*.exe"):
            shutil.copy2(file, DIST_DIR / file.name)
            print(f"Frontend copiado: {file.name}")


def create_desktop_shortcut_hint() -> None:
    """Dica de atalho para Windows (via powershell)."""
    print("Para criar atalho na área de trabalho, execute o comando abaixo no PowerShell:")
    print(
        r"$s=(New-Object -COM WScript.Shell).CreateShortcut("
        r"'$env:USERPROFILE\Desktop\PelucheGPT.lnk');"
        r"$s.TargetPath='"
        + str((DIST_DIR / "peluchegpt.exe").resolve())
        + r"';$s.Save()"
    )


def main() -> None:
    """Orquestra build completo e entrega final do app."""
    ensure_ollama()
    install_python_dependencies()
    backend_exe = package_backend()
    package_frontend()
    assemble_dist(backend_exe)
    create_desktop_shortcut_hint()
    print("Build concluído com sucesso.")


if __name__ == "__main__":
    main()
