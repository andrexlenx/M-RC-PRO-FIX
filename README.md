# M-RC-PRO-FIX

The MJX Bugs 5W 4K uses an app called **M RC PRO** (`com.logic.mrcpro`), and some of the drone's features only work through it. On Android 13 and newer, the app's **START** button does nothing, so you can never reach the drone's dashboard (live video, photos, video recording, GPS features).

This repository contains a small script that fixes that bug in **your own copy** of the app. No APK is distributed here: you patch the app you already have.

> Not affiliated with MJX or the app developer. Use at your own risk and always fly safely and legally.

## The bug

When you press START, the app checks for three permissions that were added in Android 13 (`READ_MEDIA_IMAGES`, `READ_MEDIA_VIDEO`, `READ_MEDIA_AUDIO`). The developer forgot to declare them in the app's manifest. Android silently denies any permission that an app doesn't declare, so the permission dialog flashes and closes, the app logs `ActMain: ERROR2`, and nothing happens. The same check also blocks the photo and video buttons.

A second bug breaks the **share** button in the app's gallery on Android 10 and newer: the app tries to register the file with Android's media database in an old way that modern Android rejects ("Mutation of _data is not allowed"), so nothing happens.

## The fix

The script makes two changes to the app's code:

1. **START button:** on Android 13+ the storage check now reports "granted". This is safe because the app saves its photos and videos in its own folder, which needs no storage permission on modern Android.
2. **Share button:** sharing a photo or video now registers it with Android the modern way (a copy is saved to `Pictures/` or `Movies/`) and passes that copy to the app you share with.

The app's connection to the drone, its video and its controls are not modified.

## Supported version

| App | Version | `classes.dex` SHA-256 |
|---|---|---|
| M RC PRO | 25.10.31 (113) | `7db6316fbb3a32202d436cd402d6412a6b962b04d5a24b1a21238023c14a7449` |

The script checks this hash and refuses to patch any other version.

Tested on a phone running Android 16. If it works (or doesn't) on your phone, please open an issue with your phone model and Android version.

## Requirements (on your PC)

- [Python 3.8+](https://www.python.org/downloads/)
- **Android SDK build-tools** (provide `zipalign` and `apksigner`) and **platform-tools** (`adb`). The easiest way to get both is [Android Studio](https://developer.android.com/studio): *Settings → Languages & Frameworks → Android SDK → SDK Tools* and tick *Android SDK Build-Tools* and *Android SDK Platform-Tools*.
- **Java** (for `keytool`). Android Studio includes one; otherwise install any JDK 11+.
- A USB cable, and **USB debugging** enabled on the phone (*Settings → About phone →* tap *Build number* 7 times, then *Developer options → USB debugging*).

## Step by step

### 1. Get the original APK from your phone

Install M RC PRO from the Play Store (if it isn't already installed), connect the phone to the PC and run:

```sh
adb shell pm path com.logic.mrcpro
```

It prints something like `package:/data/app/~~xxxx/com.logic.mrcpro-yyyy/base.apk`. Copy it to the PC (use the path without `package:`):

```sh
adb pull /data/app/~~xxxx/com.logic.mrcpro-yyyy/base.apk mrcpro-original.apk
```

> If `pm path` prints more than one line (split APKs), the script isn't supported on your phone yet. Please open an issue.

### 2. Patch it

```sh
python patch_mrcpro.py mrcpro-original.apk
```

This creates `mrcpro-fixed.apk`. The first time, it also creates `mrcpro-fix.keystore`, a signing key that stays on your PC. **Keep it and don't share it**: you'll need the same key to install a future fixed version over this one without losing your settings.

### 3. Replace the app

The fixed APK is signed with your key instead of the developer's, so Android requires uninstalling the original first. This deletes the app's settings and **any photos/videos saved inside the app**, so copy those first if you need them (see "Getting your photos and videos" below).

```sh
adb uninstall com.logic.mrcpro
adb install mrcpro-fixed.apk
```

### 4. Fly

Connect the phone to the drone's Wi-Fi, open M RC PRO and press START. Allow the location permission when asked (it's needed for GPS features), then press START again. The safety tutorial and then the dashboard should open.

## Known limitations

- **The Google Maps minimap is blank.** The app's Maps key is tied to the developer's signature, which a re-signed app can't have. Video, photos, recording and flight controls don't depend on it.
- **Sharing saves an extra copy.** Each time you share a photo or video, a copy is added to `Pictures/` or `Movies/`, next to the copy the app already makes automatically (see below). Sharing the same file twice creates two copies.
- **No automatic updates.** The Play Store won't update the fixed app. If a new official version appears, check whether it fixes the bug; if it doesn't, this script will need updating for it.

## Getting your photos and videos

The app keeps the originals of the photos and videos you take in its own folder, which is what its built-in gallery shows:

```
Android/data/com.logic.mrcpro/files/Pictures/
```

This folder is **deleted if you uninstall the app**.

The app also saves a copy of each photo in `Pictures/` (named like `2026112119172202.jpg.png`) and of each video in `Movies/`. Those copies appear in the phone's Gallery app. To copy the originals to a PC:

```sh
adb pull /sdcard/Android/data/com.logic.mrcpro/files/Pictures/ drone-media
```

## Troubleshooting

| Problem | What to do |
|---|---|
| `unsupported app version (classes.dex hash mismatch)` | Your app version is different from the supported one. Open an issue with the version shown at the bottom left of the app's start screen. |
| `Android SDK build-tools ... not found` | Install the build-tools (see Requirements) or set the `ANDROID_HOME` environment variable to your SDK folder. |
| `keytool not found` | Install a JDK or set `JAVA_HOME`. |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | The original app is still installed. Run `adb uninstall com.logic.mrcpro` first. |
| START still does nothing | Make sure you allowed the location permission (*Settings → Apps → M RC PRO → Permissions*). Then check the log with `adb logcat -s ActMain` while pressing START and open an issue with the output. |

## How it works (technical)

All changes are made in place in `classes.dex`, with the same length, using only method, type and string references that already exist in the app, so nothing else in the file moves.

### 1. START button

The patched method is `com.activity.ActBasic.checkWriteExternalPermission(Z)Z`:

```smali
sget v0, Landroid/os/Build$VERSION;->SDK_INT:I
const/16 v1, 0x21                     # 33 = Android 13
if-lt v0, v1, :legacy                 # Android 12 and older: request WRITE_EXTERNAL_STORAGE (unchanged)
invoke-static {p0}, ActBasic;->lacksPermission(Context)Z   # checks READ_MEDIA_* (never granted)
move-result v0
if-eqz v0, :return_true               # patched to: goto :return_true
...                                   # requested undeclared READ_MEDIA_* permissions -> always denied
```

One 4-byte change at `classes.dex` offset `0x20081A` (`38000b00` → `280b0000`, i.e. `if-eqz v0, +0xb` → `goto +0xb; nop`).

### 2. Share button

The share code (`lgUtil.ShareImages`/`ShareVideos` and `ActPhoto.onShareBtn`) gets a `content://` URI from `com.lgUtil.lgUtil.getImageContentUri` / `getVideoContentUri(Context, String path, Uri collection)`. The original methods look the file up with `_data=?` and, if it's not found, insert a row with `_data` = the app-private path. Android 10+ rejects that. Their bodies (offsets `0x315420` and `0x3155B0`) are replaced with the equivalent of:

```java
ContentValues cv = new ContentValues();
cv.put("_display_name", new File(path).getName());       // MIME type is inferred from the extension
Uri uri = ctx.getContentResolver().insert(collection, cv); // lands in Pictures/ or Movies/
if (uri != null) MediaUtils.copyFile(path, ctx.getContentResolver().openOutputStream(uri));
return uri;
```

That's 37 code units; the rest of each original method (108 and 110 units) is filled with `nop`. `MediaUtils.copyFile` is the app's own helper, already used by its `saveImageToGallery`.

### Packaging

The script then fixes the DEX header checksums, rebuilds the APK without the old signature, aligns it with `zipalign`, and signs it with `apksigner`.

## License

[MIT](LICENSE) (applies to the script and documentation in this repository only, not to the M RC PRO app).
