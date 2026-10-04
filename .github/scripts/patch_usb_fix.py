from pathlib import Path
import re
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')

def read(rel):
    return (root / rel).read_text(encoding='utf-8')

def write(rel, text):
    (root / rel).write_text(text, encoding='utf-8')

storage_rel = 'app/src/main/java/app/gamenative/utils/StorageUtils.kt'
s = read(storage_rel)

# Samsung OTG: allow both the normal /storage/<UUID> namespace and the vold
# /mnt/media_rw/<UUID> namespace for detection. The latter is used only when
# its app-specific Android/data/<pkg>/files directory is actually writable.
old = '''    fun isExternalInstallTarget(storageManager: StorageManager?, appFilesDir: File): Boolean {
        val volume = storageManager?.getStorageVolume(appFilesDir)
            ?: return runCatching { Environment.isExternalStorageRemovable(appFilesDir) }.getOrDefault(false)
        if (!volume.isPrimary) return true
'''
new = '''    fun isMountedInstallCandidate(storageManager: StorageManager?, appFilesDir: File): Boolean {
        if (runCatching { Environment.getExternalStorageState(appFilesDir) == Environment.MEDIA_MOUNTED }.getOrDefault(false)) return true
        val path = appFilesDir.absolutePath
        return runCatching {
            storageManager?.storageVolumes?.any { volume ->
                val uuid = volume.uuid
                volume.state == Environment.MEDIA_MOUNTED && !uuid.isNullOrBlank() &&
                    (path.startsWith("/storage/$uuid/") || path.startsWith("/mnt/media_rw/$uuid/"))
            } == true
        }.getOrDefault(false)
    }

    fun isExternalInstallTarget(storageManager: StorageManager?, appFilesDir: File): Boolean {
        val path = appFilesDir.absolutePath
        val matched = runCatching {
            storageManager?.storageVolumes?.firstOrNull { volume ->
                val uuid = volume.uuid
                volume.state == Environment.MEDIA_MOUNTED && !uuid.isNullOrBlank() &&
                    (path.startsWith("/storage/$uuid/") || path.startsWith("/mnt/media_rw/$uuid/"))
            }
        }.getOrNull()
        if (matched != null && !matched.isPrimary) return true

        val volume = runCatching { storageManager?.getStorageVolume(appFilesDir) }.getOrNull()
            ?: return runCatching { Environment.isExternalStorageRemovable(appFilesDir) }.getOrDefault(false)
        if (!volume.isPrimary) return true
'''
if old not in s:
    raise SystemExit('StorageUtils target 1 not found')
s = s.replace(old, new, 1)

s = s.replace(
    '                storageManager.getUuidForPath(appFilesDir)',
    '                storageManager?.getUuidForPath(appFilesDir)',
    1,
)

# Do not map /mnt/media_rw app-private storage to /mnt/media_rw/<uuid>/GameNative.
# That public root is what caused the native Steam downloader to stall after depot prep.
old = '''    fun publicInstallRoot(appFilesDir: File): File? {
        val path = appFilesDir.absolutePath
        val idx = path.indexOf("/Android/data/")
        if (idx <= 0) return null
        return File(path.substring(0, idx), PUBLIC_INSTALL_DIR_NAME)
    }
'''
new = '''    fun publicInstallRoot(appFilesDir: File): File? {
        val path = appFilesDir.absolutePath
        if (path.startsWith("/mnt/media_rw/")) return null
        val idx = path.indexOf("/Android/data/")
        if (idx <= 0) return null
        return File(path.substring(0, idx), PUBLIC_INSTALL_DIR_NAME)
    }
'''
if old not in s:
    raise SystemExit('publicInstallRoot target not found')
s = s.replace(old, new, 1)

old = '''    fun ensureInstallRoot(dir: File): Boolean {
        if (!dir.isDirectory && !dir.mkdirs()) return false
        runCatching { File(dir, ".nomedia").createNewFile() }
        return true
    }
'''
new = '''    fun ensureInstallRoot(dir: File): Boolean {
        if (!dir.isDirectory && !dir.mkdirs()) return false
        val probe = File(dir, ".gn_write_probe_${System.nanoTime()}")
        val writable = runCatching {
            probe.writeText("ok")
            probe.delete()
            true
        }.getOrDefault(false)
        if (!writable) {
            runCatching { probe.delete() }
            return false
        }
        runCatching { File(dir, ".nomedia").createNewFile() }
        return true
    }
'''
if old not in s:
    raise SystemExit('ensureInstallRoot target not found')
s = s.replace(old, new, 1)

old = '''                    val volumeDir = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        volume.directory
                    } else {
                        // Use reflection for older APIs (26-29) if getExternalFilesDirs missed it
                        try {
                            val getPath = volume.javaClass.getMethod("getPath")
                            (getPath.invoke(volume) as? String)?.let { File(it) }
                        } catch (re: Exception) {
                            null
                        }
                    } ?: continue

                    // The app-specific dedicated directory is /Android/data/<package_name>/files
                    val appFilesDir = File(volumeDir, "Android/data/${context.packageName}/files")
                    if (!result.contains(appFilesDir) && (appFilesDir.exists() || appFilesDir.mkdirs())) {
                        result.add(appFilesDir)
                    }
'''
new = '''                    val roots = mutableListOf<File>()
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        volume.directory?.let { roots.add(it) }
                    } else {
                        try {
                            val getPath = volume.javaClass.getMethod("getPath")
                            (getPath.invoke(volume) as? String)?.let { roots.add(File(it)) }
                        } catch (_: Exception) { }
                    }

                    val uuid = volume.uuid
                    if (!uuid.isNullOrBlank()) {
                        roots.add(File("/storage/$uuid"))
                        roots.add(File("/mnt/media_rw/$uuid"))
                    }

                    for (volumeRoot in roots.distinctBy { it.absolutePath }) {
                        val appFilesDir = File(volumeRoot, "Android/data/${context.packageName}/files")
                        if (!result.contains(appFilesDir) && ensureInstallRoot(appFilesDir)) {
                            result.add(appFilesDir)
                        }
                    }
'''
if old not in s:
    raise SystemExit('StorageUtils target 2 not found')
s = s.replace(old, new, 1)
write(storage_rel, s)

filter_re = re.compile(
    r'(?P<indent>[ \t]*)\.filter \{ Environment\.getExternalStorageState\(it\) == Environment\.MEDIA_MOUNTED \}\r?\n'
    r'(?P=indent)\.filter \{ StorageUtils\.isExternalInstallTarget\(sm, it\) \}'
)
for rel in [
    'app/src/main/java/app/gamenative/service/DownloadService.kt',
    'app/src/main/java/app/gamenative/ui/screen/settings/SettingsGroupInterface.kt',
]:
    t = read(rel)
    m = filter_re.search(t)
    if not m:
        raise SystemExit(f'filter target not found in {rel}')
    indent = m.group('indent')
    replacement = (
        f'{indent}.filter {{ StorageUtils.isMountedInstallCandidate(sm, it) }}\n'
        f'{indent}.filter {{ StorageUtils.isExternalInstallTarget(sm, it) }}'
    )
    write(rel, filter_re.sub(replacement, t, count=1))

settings_rel = 'app/src/main/java/app/gamenative/ui/screen/settings/SettingsGroupInterface.kt'
t = read(settings_rel)
t = t.replace(
    'sm?.getStorageVolume(dir)?.getDescription(ctx) ?: externalStorageFallbackLabel',
    'runCatching { sm?.getStorageVolume(dir)?.getDescription(ctx) }.getOrNull() ?: externalStorageFallbackLabel',
    1,
)
write(settings_rel, t)

gradle_rel = 'app/build.gradle.kts'
t = read(gradle_rel)
old = '''        debug {
            isDebuggable = true'''
new = '''        debug {
            applicationIdSuffix = ".usbfixv4"
            versionNameSuffix = "-usbfixv4"
            isDebuggable = true'''
if old not in t:
    raise SystemExit('build.gradle debug target not found')
write(gradle_rel, t.replace(old, new, 1))

strings_rel = 'app/src/main/res/values/strings.xml'
t = read(strings_rel)
old_name = '<string name="app_name">GameNative</string>'
new_name = '<string name="app_name">GameNative USB V4</string>'
if old_name not in t:
    raise SystemExit('app_name target not found')
write(strings_rel, t.replace(old_name, new_name, 1))

print('USB V4 patch applied: Samsung detection restored; /mnt media_rw kept app-private and write-probed')
