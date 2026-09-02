from __future__ import annotations

import os
import shutil
import stat
import tempfile
import weakref
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


DEFAULT_CHROME_BINARY = "/usr/local/bin/mooncen-chrome"
DEFAULT_CHROMEDRIVER = "/usr/local/bin/mooncen-chromedriver"
DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS = 45
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 30
_SANDBOX_DISABLING_ARGUMENTS = frozenset(
    {
        "--allow-running-insecure-content",
        "--disable-gpu-sandbox",
        "--disable-namespace-sandbox",
        "--disable-sandbox",
        "--disable-seccomp-filter-sandbox",
        "--disable-seccomp-sandbox",
        "--disable-site-isolation-trials",
        "--disable-setuid-sandbox",
        "--disable-web-security",
        "--ignore-certificate-errors",
        "--no-sandbox",
        "--no-zygote",
        "--single-process",
    }
)
_BROWSER_ENVIRONMENT_KEYS = frozenset(
    {
        "APPDATA",
        "ComSpec",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "PATH",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "SystemDrive",
        "SystemRoot",
        "TMPDIR",
        "TEMP",
        "TMP",
        "TZ",
        "USERPROFILE",
        "WINDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "windir",
    }
)


def _first_existing_file(candidates: list[Path]) -> str | None:
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _windows_chrome_binary() -> str | None:
    candidates: list[Path] = []
    for root in (
        os.getenv("PROGRAMFILES"),
        os.getenv("PROGRAMFILES(X86)"),
        os.getenv("LOCALAPPDATA"),
    ):
        if not root:
            continue
        candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return _first_existing_file(candidates)


def _windows_chromedriver() -> str | None:
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [repo_root / "chromedriver.exe"]
    candidates.extend(sorted((repo_root / "chromedriver" / "win64").glob("*/chromedriver.exe"), reverse=True))
    return _first_existing_file(candidates)


def _default_chrome_binary() -> str:
    if os.name == "nt":
        return _windows_chrome_binary() or DEFAULT_CHROME_BINARY
    return DEFAULT_CHROME_BINARY


def _default_chromedriver() -> str:
    if os.name == "nt":
        return _windows_chromedriver() or DEFAULT_CHROMEDRIVER
    return DEFAULT_CHROMEDRIVER


def _timeout_seconds(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= 300:
        raise RuntimeError(f"{name} must be between 1 and 300")
    return value


def configure_driver_timeouts(driver, *, page_load_timeout: int | None = None, script_timeout: int | None = None):
    page_load_timeout = page_load_timeout or _timeout_seconds(
        "SELENIUM_PAGE_LOAD_TIMEOUT_SECONDS", DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS
    )
    script_timeout = script_timeout or _timeout_seconds(
        "SELENIUM_SCRIPT_TIMEOUT_SECONDS", DEFAULT_SCRIPT_TIMEOUT_SECONDS
    )
    driver.set_page_load_timeout(page_load_timeout)
    driver.set_script_timeout(script_timeout)
    return driver


def _required_root_executable(value: str, label: str) -> str:
    candidate = Path(value)
    if not candidate.is_absolute():
        raise RuntimeError(f"{label} must be an absolute path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"{label} is not installed") from exc
    if not resolved.is_file() or (os.name != "nt" and not os.access(resolved, os.X_OK)):
        raise RuntimeError(f"{label} must be an executable regular file")
    if os.name == "nt":
        return str(resolved)
    protected_paths = {resolved, *resolved.parents, *candidate.parents}
    try:
        for component in protected_paths:
            component_metadata = component.stat()
            if component_metadata.st_uid != 0 or stat.S_IMODE(component_metadata.st_mode) & 0o022:
                raise RuntimeError(
                    f"{label} and its parent path must be root-owned and not group/world-writable"
                )
    except OSError as exc:
        raise RuntimeError(f"{label} path cannot be verified") from exc
    return str(resolved)


def _reject_sandbox_disabling_arguments(options: Options) -> None:
    for argument in options.arguments:
        argument_name = argument.split("=", 1)[0]
        if argument_name in _SANDBOX_DISABLING_ARGUMENTS:
            raise RuntimeError(f"Chrome sandbox disabling argument is forbidden: {argument_name}")


def _browser_service_environment() -> dict[str, str]:
    # ChromeDriver and every Chrome child inherit this mapping. Keep crawler DB,
    # OAuth, API, and provider credentials out of pages' browser process tree.
    environment = {key: os.environ[key] for key in _BROWSER_ENVIRONMENT_KEYS if key in os.environ}
    if os.name != "nt" and not environment.get("TMPDIR") and environment.get("HOME"):
        environment["TMPDIR"] = environment["HOME"]
    return environment


def _has_chrome_argument(options: Options, name: str) -> bool:
    return any(argument.split("=", 1)[0] == name for argument in options.arguments)


def _attach_profile_cleanup(driver, profile_dir: str) -> None:
    cleanup = weakref.finalize(driver, shutil.rmtree, profile_dir, True)
    original_quit = driver.quit

    def quit_and_cleanup():
        try:
            return original_quit()
        finally:
            cleanup()

    driver.quit = quit_and_cleanup


def build_chrome_driver(options: Options):
    chrome_binary = os.getenv("CHROME_BINARY") or _default_chrome_binary()
    chromedriver = os.getenv("CHROMEDRIVER") or _default_chromedriver()
    page_load_timeout = _timeout_seconds(
        "SELENIUM_PAGE_LOAD_TIMEOUT_SECONDS", DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS
    )
    script_timeout = _timeout_seconds("SELENIUM_SCRIPT_TIMEOUT_SECONDS", DEFAULT_SCRIPT_TIMEOUT_SECONDS)

    _reject_sandbox_disabling_arguments(options)
    chrome_binary = _required_root_executable(chrome_binary, "CHROME_BINARY")
    chromedriver = _required_root_executable(chromedriver, "CHROMEDRIVER")
    options.binary_location = chrome_binary
    browser_environment = _browser_service_environment()
    profile_dir: str | None = None
    if not _has_chrome_argument(options, "--user-data-dir"):
        profile_dir = tempfile.mkdtemp(
            prefix="mooncen-chrome-profile-",
            dir=browser_environment.get("TMPDIR"),
        )
        options.add_argument(f"--user-data-dir={profile_dir}")
    # Always pass an explicit, root-owned driver. Falling back to Selenium
    # Manager would download and execute an unpinned binary at crawler runtime.
    service = Service(executable_path=chromedriver, env=browser_environment)
    try:
        driver = webdriver.Chrome(service=service, options=options)
    except Exception:
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)
        raise
    if profile_dir:
        _attach_profile_cleanup(driver, profile_dir)

    try:
        return configure_driver_timeouts(
            driver,
            page_load_timeout=page_load_timeout,
            script_timeout=script_timeout,
        )
    except Exception:
        driver.quit()
        raise
