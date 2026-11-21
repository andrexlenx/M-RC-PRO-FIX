#!/usr/bin/env python3
"""
M-RC-PRO-FIX - patches the M RC PRO app (com.logic.mrcpro) so the START button
works on Android 13+.

The bug: on Android 13+ the app requires READ_MEDIA_IMAGES/VIDEO/AUDIO before
opening the drone dashboard (and before taking photos/videos), but those
permissions are not declared in its manifest. Android silently denies
undeclared permissions, so START does nothing (logcat: "ActMain: ERROR2").

The fix: ActBasic.checkWriteExternalPermission() reports "granted" on
Android 13+. That is safe because the app saves its photos/videos in its own
folder, which needs no storage permission. Android 12 and older keep the
original behaviour.

It also fixes the gallery Share button (Android 10+), which failed because the
app registered files with MediaStore using the rejected "_data" column: shared
files are now copied into MediaStore the modern way and that URI is shared.

Usage:
    python patch_mrcpro.py <original.apk> [-o mrcpro-fixed.apk]

Requirements: Python 3.8+, Java (keytool), Android SDK build-tools
(zipalign + apksigner).
"""
import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

# Supported build: M RC PRO 25.10.31 (versionCode 113)
SUPPORTED_DEX_SHA256 = "7db6316fbb3a32202d436cd402d6412a6b962b04d5a24b1a21238023c14a7449"

# New body for lgUtil.getImageContentUri / getVideoContentUri
# (Context v8, String path v9, Uri collection v10) -> Uri.
# The original inserted a MediaStore row with "_data" = app-private path, which
# Android 10+ rejects ("Mutation of _data is not allowed"), so sharing failed.
# Replacement (only existing method/type/string ids are used):
#   ContentValues cv = new ContentValues();
#   cv.put("_display_name", new File(path).getName());
#   Uri uri = ctx.getContentResolver().insert(collection, cv);
#   if (uri != null) MediaUtils.copyFile(path, resolver.openOutputStream(uri));
#   return uri;
SHARE_URI_BODY = bytes.fromhex(
    "22006500"        # new-instance v0, Landroid/content/ContentValues;
    "701030020000"    # invoke-direct {v0}, ContentValues.<init>()V
    "2201bd16"        # new-instance v1, Ljava/io/File;
    "7020aba59100"    # invoke-direct {v1, v9}, File.<init>(String)V
    "6e10b8a50100"    # invoke-virtual {v1}, File.getName()String
    "0c01"            # move-result-object v1
    "1a02ba4f"        # const-string v2, "_display_name"
    "6e3031022001"    # invoke-virtual {v0, v2, v1}, ContentValues.put(String,String)V
    "6e1045020800"    # invoke-virtual {v8}, Context.getContentResolver()
    "0c02"            # move-result-object v2
    "6e302502a200"    # invoke-virtual {v2, v10, v0}, ContentResolver.insert(Uri,ContentValues)Uri
    "0c00"            # move-result-object v0
    "38000900"        # if-eqz v0, +9 (-> return-object v0)
    "6e202b020200"    # invoke-virtual {v2, v0}, ContentResolver.openOutputStream(Uri)
    "0c01"            # move-result-object v1
    "7120e09a1900"    # invoke-static {v9, v1}, MediaUtils.copyFile(String,OutputStream)Z
    "1100"            # return-object v0
)


def method_body(body, insns_units):
    """Pad a new method body with nops to the original insns_size."""
    assert len(body) % 2 == 0 and len(body) <= insns_units * 2
    return body + b"\x00" * (insns_units * 2 - len(body))


# (offset, expected original bytes at offset, replacement bytes)
PATCHES = [
    # 1. START button: ActBasic.checkWriteExternalPermission(Z)Z, insn 0x000b
    #    if-eqz v0, +0x0b -> goto +0x0b ; nop   (always "return true" on SDK >= 33)
    (0x20081A, bytes.fromhex("38000b00"), bytes.fromhex("280b0000")),
    # 2. Gallery share (photos): lgUtil.getImageContentUri, insns_size 108
    (0x315420, bytes.fromhex("120038086a00"), method_body(SHARE_URI_BODY, 108)),
    # 3. Gallery share (videos): lgUtil.getVideoContentUri, insns_size 110
    (0x3155B0, bytes.fromhex("120038086c00"), method_body(SHARE_URI_BODY, 110)),
]

DEFAULT_KS_PASS = "android"
KEY_ALIAS = "mrcprofix"


def fail(msg):
    sys.exit(f"ERROR: {msg}")


def find_build_tools():
    candidates = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            candidates.append(Path(os.environ[var]))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk")
    candidates += [Path.home() / "Library/Android/sdk", Path.home() / "Android/Sdk"]

    exe = ".exe" if os.name == "nt" else ""
    signer = "apksigner.bat" if os.name == "nt" else "apksigner"
    for sdk in candidates:
        bt_root = sdk / "build-tools"
        if not bt_root.is_dir():
            continue

        def version_key(p):
            return [int(x) if x.isdigit() else 0 for x in p.name.replace("-", ".").split(".")]

        for bt in sorted(bt_root.iterdir(), key=version_key, reverse=True):
            za, sg = bt / f"zipalign{exe}", bt / signer
            if za.is_file() and sg.is_file():
                return za, sg
    fail("Android SDK build-tools (zipalign/apksigner) not found. "
         "Install them with Android Studio's SDK Manager or set ANDROID_HOME.")


def find_keytool():
    exe = "keytool.exe" if os.name == "nt" else "keytool"
    if os.environ.get("JAVA_HOME"):
        kt = Path(os.environ["JAVA_HOME"]) / "bin" / exe
        if kt.is_file():
            return str(kt)
    kt = shutil.which("keytool")
    if kt:
        return kt
    # Common install locations (JDKs, Android Studio's bundled JBR)
    roots = []
    if os.name == "nt":
        for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if pf:
                roots += [Path(pf) / "Java", Path(pf) / "Eclipse Adoptium",
                          Path(pf) / "Microsoft", Path(pf) / "Android" / "Android Studio"]
    else:
        roots += [Path("/usr/lib/jvm"), Path("/Library/Java/JavaVirtualMachines"),
                  Path("/Applications/Android Studio.app/Contents"), Path.home() / "android-studio"]
    for root in roots:
        if root.is_dir():
            found = sorted(root.glob(f"**/bin/{exe}"), reverse=True)
            if found:
                return str(found[0])
    fail("keytool not found. Install a Java JDK and/or set JAVA_HOME.")


def run(cmd):
    res = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if res.returncode != 0:
        fail(f"command failed: {Path(str(cmd[0])).name}\n{res.stdout}\n{res.stderr}")
    return res


def patch_dex(dex: bytearray):
    sha = hashlib.sha256(dex).hexdigest()
    if sha != SUPPORTED_DEX_SHA256:
        fail("unsupported app version (classes.dex hash mismatch).\n"
             "This patch only supports M RC PRO 25.10.31 (113).\n"
             f"Got: {sha}")
    for off, old, new in PATCHES:
        if dex[off:off + len(old)] != old:
            fail(f"unexpected bytes at 0x{off:x}")
        dex[off:off + len(new)] = new  # same length: nothing else in the file moves
    # Fix DEX header: SHA-1 of [32:] at offset 12, Adler-32 of [12:] at offset 8
    dex[12:32] = hashlib.sha1(dex[32:]).digest()
    struct.pack_into("<I", dex, 8, zlib.adler32(bytes(dex[12:])) & 0xFFFFFFFF)


def rebuild_apk(src: Path, dst: Path):
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        if "classes.dex" not in names:
            fail("classes.dex not found - is this the right APK?")
        dex = bytearray(zin.read("classes.dex"))
        patch_dex(dex)
        with zipfile.ZipFile(dst, "w") as zout:
            for info in zin.infolist():
                name = info.filename
                # drop the original (now invalid) v1 signature
                if name.startswith("META-INF/") and (
                    name.endswith((".SF", ".RSA", ".DSA", ".EC")) or name == "META-INF/MANIFEST.MF"
                ):
                    continue
                data = bytes(dex) if name == "classes.dex" else zin.read(name)
                new = zipfile.ZipInfo(name, date_time=info.date_time)
                new.compress_type = info.compress_type  # keep .so/resources.arsc STORED
                new.external_attr = info.external_attr
                zout.writestr(new, data)


def main():
    ap = argparse.ArgumentParser(description="Fix M RC PRO START button on Android 13+")
    ap.add_argument("apk", type=Path, help="original M RC PRO APK (base.apk)")
    ap.add_argument("-o", "--output", type=Path, default=Path("mrcpro-fixed.apk"))
    ap.add_argument("--keystore", type=Path, default=Path("mrcpro-fix.keystore"),
                    help="signing keystore (created if missing; keep it private)")
    ap.add_argument("--ks-pass", default=DEFAULT_KS_PASS, help="keystore password")
    args = ap.parse_args()

    if not args.apk.is_file():
        fail(f"file not found: {args.apk}")
    zipalign, apksigner = find_build_tools()

    if not args.keystore.is_file():
        print(f"[*] Creating signing key: {args.keystore}")
        run([find_keytool(), "-genkeypair", "-keystore", args.keystore, "-alias", KEY_ALIAS,
             "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-storepass", args.ks_pass, "-keypass", args.ks_pass, "-dname", "CN=M-RC-PRO-FIX"])

    with tempfile.TemporaryDirectory() as tmp:
        unsigned, aligned = Path(tmp) / "unsigned.apk", Path(tmp) / "aligned.apk"
        print("[*] Patching classes.dex")
        rebuild_apk(args.apk, unsigned)
        print("[*] Aligning")
        run([zipalign, "-f", "-p", "4", unsigned, aligned])
        print("[*] Signing")
        run([apksigner, "sign", "--ks", args.keystore, "--ks-pass", f"pass:{args.ks_pass}",
             "--ks-key-alias", KEY_ALIAS, "--v4-signing-enabled", "false",
             "--out", args.output, aligned])
    run([apksigner, "verify", args.output])
    print(f"[+] Done: {args.output}")
    print("    Uninstall the original app, then install this APK "
          "(e.g. 'adb install mrcpro-fixed.apk').")


if __name__ == "__main__":
    main()
