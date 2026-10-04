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

# Samsung OTG can be reported only under /mnt/media_rw/<UUID>. Detect that mount
# independently from whether the app currently has broad storage permission.
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

# Validate the final selected install root with a real write, but do NOT use this
# probe during volume discovery. Discovery must stay visible before permission is granted.
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

# Enumerate both Android's app-facing /storage view and Samsung's /mnt/media_rw view.
# Crucially, add the candidate even before mkdir/write succeeds; Legacy will request
# MANAGE_EXTERNAL_STORAGE when the user enables the external target.
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
                        result.add(appFilesDir)
                    }
'''
if old not in s:
    raise SystemExit('StorageUtils target 2 not found')
s = s.replace(old, new, 1)
write(storage_rel, s)

# Use mount-aware filtering instead of Environment.getExternalStorageState(File), which
# returns unknown for Samsung's /mnt/media_rw path.
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

# Imports needed for the standard Android "All files access" grant used by the Legacy flavor.
t = t.replace(
    'import android.content.res.Configuration\n',
    'import android.content.res.Configuration\nimport android.net.Uri\nimport android.os.Build\nimport android.provider.Settings\n',
    1,
)

t = t.replace(
    'sm?.getStorageVolume(dir)?.getDescription(ctx) ?: externalStorageFallbackLabel',
    'runCatching { sm?.getStorageVolume(dir)?.getDescription(ctx) }.getOrNull() ?: externalStorageFallbackLabel',
    1,
)

old_switch = '''        var useExternalStorage by rememberSaveable { mutableStateOf(PrefManager.useExternalStorage) }
        SettingsSwitch(
            colors = settingsTileColorsAlt(),
            enabled = dirs.isNotEmpty(),
            title = { Text(text = stringResource(R.string.settings_interface_external_storage_title)) },
            subtitle = {
                if (dirs.isEmpty())
                    Text(stringResource(R.string.settings_interface_no_external_storage))
                else
                    Text(stringResource(R.string.settings_interface_external_storage_subtitle))
            },
            state = useExternalStorage,
            onCheckedChange = {
                useExternalStorage = it
                PrefManager.useExternalStorage = it
                if (it && dirs.isNotEmpty()) {
                    PrefManager.externalStoragePath = StorageUtils.preferredInstallRoot(dirs[0])
                }
            },
        )
'''
new_switch = '''        var useExternalStorage by rememberSaveable { mutableStateOf(PrefManager.useExternalStorage) }

        // Legacy Android 11+ needs MANAGE_EXTERNAL_STORAGE before a raw USB path can be
        // passed to the native Steam downloader. Keep the volume visible first, then ask
        // for the permission only when the user enables external installs.
        val manageStorageLauncher = rememberLauncherForActivityResult(
            contract = ActivityResultContracts.StartActivityForResult(),
        ) {
            val granted = Build.VERSION.SDK_INT < Build.VERSION_CODES.R || Environment.isExternalStorageManager()
            if (granted && dirs.isNotEmpty()) {
                val selected = StorageUtils.preferredInstallRoot(dirs[0])
                if (StorageUtils.ensureInstallRoot(File(selected))) {
                    useExternalStorage = true
                    PrefManager.useExternalStorage = true
                    PrefManager.externalStoragePath = selected
                }
            }
        }

        SettingsSwitch(
            colors = settingsTileColorsAlt(),
            enabled = dirs.isNotEmpty(),
            title = { Text(text = stringResource(R.string.settings_interface_external_storage_title)) },
            subtitle = {
                if (dirs.isEmpty())
                    Text(stringResource(R.string.settings_interface_no_external_storage))
                else
                    Text(stringResource(R.string.settings_interface_external_storage_subtitle))
            },
            state = useExternalStorage,
            onCheckedChange = { enable ->
                if (!enable) {
                    useExternalStorage = false
                    PrefManager.useExternalStorage = false
                } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && !Environment.isExternalStorageManager()) {
                    val intent = Intent(
                        Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:${ctx.packageName}"),
                    )
                    manageStorageLauncher.launch(intent)
                } else if (dirs.isNotEmpty()) {
                    val selected = StorageUtils.preferredInstallRoot(dirs[0])
                    if (StorageUtils.ensureInstallRoot(File(selected))) {
                        useExternalStorage = true
                        PrefManager.useExternalStorage = true
                        PrefManager.externalStoragePath = selected
                    }
                }
            },
        )
'''
if old_switch not in t:
    raise SystemExit('external storage switch target not found')
t = t.replace(old_switch, new_switch, 1)
write(settings_rel, t)

# V5 is a separate install because CI debug signing keys are ephemeral.
gradle_rel = 'app/build.gradle.kts'
t = read(gradle_rel)
old = '''        debug {
            isDebuggable = true'''
new = '''        debug {
            applicationIdSuffix = ".usbfixv5"
            versionNameSuffix = "-usbfixv5"
            isDebuggable = true'''
if old not in t:
    raise SystemExit('build.gradle debug target not found')
write(gradle_rel, t.replace(old, new, 1))

strings_rel = 'app/src/main/res/values/strings.xml'
t = read(strings_rel)
old_name = '<string name="app_name">GameNative</string>'
new_name = '<string name="app_name">GameNative USB V5</string>'
if old_name not in t:
    raise SystemExit('app_name target not found')
write(strings_rel, t.replace(old_name, new_name, 1))

print('USB V5 patch applied: Samsung OTG discovery + Legacy all-files permission + write validation')
