from pathlib import Path
import re


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"{label}: patch target not found in {path}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


storage = Path("app/src/main/java/app/gamenative/utils/StorageUtils.kt")
s = storage.read_text(encoding="utf-8")

old = '''    fun isExternalInstallTarget(storageManager: StorageManager?, appFilesDir: File): Boolean {
        val volume = storageManager?.getStorageVolume(appFilesDir)
            ?: return runCatching { Environment.isExternalStorageRemovable(appFilesDir) }.getOrDefault(false)'''
new = '''    fun isExternalInstallTarget(storageManager: StorageManager?, appFilesDir: File): Boolean {
        // Samsung USB OTG can be mounted only under /mnt/media_rw/<UUID>, with no
        // /storage/<UUID> view. Treat that public removable mount as an install target.
        if (appFilesDir.absolutePath.startsWith("/mnt/media_rw/")) return true

        val volume = runCatching { storageManager?.getStorageVolume(appFilesDir) }.getOrNull()
            ?: return runCatching { Environment.isExternalStorageRemovable(appFilesDir) }.getOrDefault(false)'''
if old not in s:
    raise SystemExit("StorageUtils isExternalInstallTarget patch target not found")
s = s.replace(old, new, 1)

old = '''                for (volume in sm.storageVolumes) {
                    if (volume.state != Environment.MEDIA_MOUNTED) continue

                    val volumeDir = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {'''
new = '''                for (volume in sm.storageVolumes) {
                    if (volume.state != Environment.MEDIA_MOUNTED) continue

                    // Some Samsung builds expose USB OTG only at /mnt/media_rw/<UUID>.
                    // Synthesize an app-files-shaped path from that root; callers map it
                    // to the public <volume>/GameNative install root.
                    val volumeUuid = volume.uuid
                    if (!volumeUuid.isNullOrBlank()) {
                        val mediaRwRoot = File("/mnt/media_rw/$volumeUuid")
                        if (mediaRwRoot.exists()) {
                            result.add(File(mediaRwRoot, "Android/data/${context.packageName}/files"))
                        }
                    }

                    val volumeDir = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {'''
if old not in s:
    raise SystemExit("StorageUtils storageVolumes patch target not found")
s = s.replace(old, new, 1)
storage.write_text(s, encoding="utf-8")

# Android's Environment state API may reject /mnt/media_rw paths even though the
# StorageManager volume is mounted. Accept those synthesized Samsung USB paths directly.
for path in [
    "app/src/main/java/app/gamenative/service/DownloadService.kt",
    "app/src/main/java/app/gamenative/ui/screen/settings/SettingsGroupInterface.kt",
]:
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    old = '''.filter { Environment.getExternalStorageState(it) == Environment.MEDIA_MOUNTED }
            .filter { StorageUtils.isExternalInstallTarget(sm, it) }'''
    new = '''.filter {
                it.absolutePath.startsWith("/mnt/media_rw/") ||
                    runCatching { Environment.getExternalStorageState(it) == Environment.MEDIA_MOUNTED }.getOrDefault(false)
            }
            .filter {
                it.absolutePath.startsWith("/mnt/media_rw/") ||
                    StorageUtils.isExternalInstallTarget(sm, it)
            }'''
    if old not in s:
        raise SystemExit(f"External storage filter patch target not found in {path}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")

# Install beside the official GameNative package so the user's existing app/data are untouched.
gradle = Path("app/build.gradle.kts")
s = gradle.read_text(encoding="utf-8")
old = '''        debug {
            isDebuggable = true'''
new = '''        debug {
            applicationIdSuffix = ".usbfix"
            versionNameSuffix = "-usbfix"
            isDebuggable = true'''
if old not in s:
    raise SystemExit("Debug build type patch target not found")
gradle.write_text(s.replace(old, new, 1), encoding="utf-8")

strings = Path("app/src/main/res/values/strings.xml")
if strings.exists():
    s = strings.read_text(encoding="utf-8")
    s2, count = re.subn(
        r'(<string\s+name="app_name"[^>]*>).*?(</string>)',
        r'\1GameNative USB Fix\2',
        s,
        count=1,
    )
    if count:
        strings.write_text(s2, encoding="utf-8")

print("Samsung USB OTG patch applied")
