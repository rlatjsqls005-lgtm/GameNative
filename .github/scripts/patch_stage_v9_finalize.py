from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
path = root / 'app/src/main/java/app/gamenative/service/SteamService.kt'
t = path.read_text(encoding='utf-8')

old_sig = '''        private fun flushStagedInstallToExternal(stagingPath: String, targetPath: String) {'''
new_sig = '''        private fun flushStagedInstallToExternal(
            stagingPath: String,
            targetPath: String,
            includeMetadata: Boolean = false,
        ) {'''
if old_sig not in t:
    raise SystemExit('V9 staging helper signature not found')
t = t.replace(old_sig, new_sig, 1)

old_filter = '''                if (child.name != ".DepotDownloader") {
                    copyAndDelete(child, File(targetRoot, child.name))
                }'''
new_filter = '''                if (includeMetadata || child.name != ".DepotDownloader") {
                    copyAndDelete(child, File(targetRoot, child.name))
                }'''
if old_filter not in t:
    raise SystemExit('V9 staging metadata filter not found')
t = t.replace(old_filter, new_filter, 1)

old_finish = '''                            // The only thing intentionally left behind between depot flushes is
                            // the native journal. The whole staging tree can go after all depots.
                            if (stageDir.exists()) {
                                NativeTreeDelete.deleteTreeFast(stageDir)
                            }'''
new_finish = '''                            // Keep .DepotDownloader on internal storage while depots are still
                            // running, then move the complete journal/manifests to the final external
                            // install. GameNative reads these files later for resume/update/exe lookup.
                            if (stageDir.exists()) {
                                Timber.i("Flushing Steam journal and manifest metadata to external storage")
                                flushStagedInstallToExternal(
                                    stagePath,
                                    appDirPath,
                                    includeMetadata = true,
                                )
                                if (stageDir.exists()) {
                                    NativeTreeDelete.deleteTreeFast(stageDir)
                                }
                            }'''
if old_finish not in t:
    raise SystemExit('V9 staging final cleanup block not found')
t = t.replace(old_finish, new_finish, 1)

# Sanity guards: this finalizer must only run on the V9 staging build.
if '.gn_stage_$appId' not in t:
    raise SystemExit('V9 staging path missing after patch')

path.write_text(t, encoding='utf-8')
print('USB V9 finalizer applied: preserve .DepotDownloader through depot runs and move metadata to external at completion')
