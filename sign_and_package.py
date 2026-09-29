import os
import sys
import shutil
import subprocess
import hashlib
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

CryptUnprotectData = ctypes.windll.crypt32.CryptUnprotectData
LocalFree = ctypes.windll.kernel32.LocalFree

def unprotect(data: bytes, entropy: bytes = b"MoonCenMonitorReleaseSigning-v1") -> str:
    data_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_byte)))
    entropy_blob = DATA_BLOB(len(entropy), ctypes.cast(ctypes.create_string_buffer(entropy, len(entropy)), ctypes.POINTER(ctypes.c_byte)))
    data_out = DATA_BLOB()
    res = CryptUnprotectData(ctypes.byref(data_in), None, ctypes.byref(entropy_blob), None, None, 0, ctypes.byref(data_out))
    if not res:
        raise RuntimeError("CryptUnprotectData failed")
    result_bytes = ctypes.string_at(data_out.pbData, data_out.cbData)
    LocalFree(data_out.pbData)
    return result_bytes.decode("utf-8")

def main():
    repo_root = r"C:\Users\gen1w\mooncen-repo"
    app_release_dir = os.path.join(repo_root, r"BOT\android_monitor_app\app\build\outputs\apk\release")
    unsigned_apk = os.path.join(app_release_dir, "app-release-unsigned.apk")
    aligned_apk = os.path.join(app_release_dir, "app-release-aligned.apk")
    signed_apk = os.path.join(app_release_dir, "mooncen-monitor-1.8.19.apk")

    build_tools = r"C:\Users\gen1w\AppData\Local\Android\Sdk\build-tools\35.0.0"
    zipalign_exe = os.path.join(build_tools, "zipalign.exe")
    apksigner_bat = os.path.join(build_tools, "apksigner.bat")

    keystore = r"C:\Users\gen1w\.android\mooncen-monitor-release.p12"
    pass_file = r"C:\Users\gen1w\.android\mooncen-monitor-release.pass.dpapi"

    print("[1] Decrypting DPAPI password envelope...")
    with open(pass_file, "rb") as f:
        enc_data = f.read()
    password = unprotect(enc_data)
    print(f"    Password decrypted successfully (length={len(password)}).")

    print("[2] Running zipalign...")
    if os.path.exists(aligned_apk):
        os.remove(aligned_apk)
    cmd_align = [zipalign_exe, "-p", "-f", "4", unsigned_apk, aligned_apk]
    subprocess.run(cmd_align, check=True)
    print("    zipalign completed.")

    print("[3] Running apksigner...")
    if os.path.exists(signed_apk):
        os.remove(signed_apk)
    shutil.copy2(aligned_apk, signed_apk)

    java_exe = r"C:\Users\gen1w\.jdks\microsoft-jdk-17.0.12\bin\java.exe"
    apksigner_jar = os.path.join(build_tools, r"lib\apksigner.jar")

    cmd_sign = [
        java_exe, "-jar", apksigner_jar, "sign",
        "--ks", keystore,
        "--ks-type", "PKCS12",
        "--ks-key-alias", "mooncen-monitor",
        "--ks-pass", f"pass:{password}",
        "--v1-signing-enabled", "true",
        "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "true",
        "--out", signed_apk,
        aligned_apk
    ]
    res = subprocess.run(cmd_sign, capture_output=True, text=True)
    if res.returncode != 0:
        print("apksigner error:", res.stderr, res.stdout)
        sys.exit(1)
    print("    apksigner sign completed.")

    print("[4] Verifying signature...")
    cmd_verify = [java_exe, "-jar", apksigner_jar, "verify", "--verbose", signed_apk]
    proc = subprocess.run(cmd_verify, capture_output=True, text=True, check=True)
    print(proc.stdout)

    # Compute SHA256 and size
    with open(signed_apk, "rb") as f:
        apk_bytes = f.read()
    sha256_hash = hashlib.sha256(apk_bytes).hexdigest()
    size_bytes = len(apk_bytes)
    print(f"Signed APK: {signed_apk}")
    print(f"Size: {size_bytes} bytes")
    print(f"SHA256: {sha256_hash}")

    # Copy to android_downloads
    downloads_dir = os.path.join(repo_root, r"BOT\android_downloads")
    versioned_apk = os.path.join(downloads_dir, "mooncen-monitor-1.8.19.apk")
    main_apk = os.path.join(downloads_dir, "mooncen-monitor.apk")
    shutil.copy2(signed_apk, versioned_apk)
    shutil.copy2(signed_apk, main_apk)
    print(f"Copied to {versioned_apk} and {main_apk}")

    # Update latest.json
    latest_json_path = os.path.join(downloads_dir, "latest.json")
    import json
    latest_data = {
        "schema_version": 1,
        "application_id": "com.mooncen.monitor",
        "version_code": 28,
        "version_name": "1.8.19",
        "min_sdk": 26,
        "apk_url": "https://mon.binary.kr/android/mooncen-monitor.apk",
        "download_url": "https://mon.binary.kr/android/mooncen-monitor.apk",
        "apk_filename": "mooncen-monitor.apk",
        "sha256": sha256_hash,
        "size_bytes": size_bytes,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "signer_sha256": "c9f655472d1ff4ead58be4e6bb2203bca1bd603cf8cc646798ead038c7cd58ee",
        "notes": "분산 크롤러 락 격리 및 mac/gen1crawler 워커 노드 모니터링 연동 최적화"
    }
    with open(latest_json_path, "w", encoding="utf-8") as f:
        json.dump(latest_data, f, ensure_ascii=False, indent=2)
    print("Updated latest.json successfully.")

if __name__ == "__main__":
    main()
