from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')

def read(rel):
    return (root / rel).read_text(encoding='utf-8')

def write(rel, text):
    (root / rel).write_text(text, encoding='utf-8')

steam_rel = 'app/src/main/java/app/gamenative/service/SteamService.kt'
t = read(steam_rel)

helper_anchor = '''        fun downloadApp(
            appId: Int,
'''
helper = '''        private fun flushStagedInstallToExternal(stagingPath: String, targetPath: String) {
            val sourceRoot = File(stagingPath)
            if (!sourceRoot.exists()) return

            val targetRoot = File(targetPath)
            if (!targetRoot.isDirectory && !targetRoot.mkdirs()) {
                throw IOException("Unable to create external install directory: $targetPath")
            }

            fun copyAndDelete(source: File, target: File) {
                if (Files.isSymbolicLink(source.toPath())) {
                    throw IOException("External exFAT staging does not support symbolic link: ${source.path}")
                }

                if (source.isDirectory) {
                    if (!target.isDirectory && !target.mkdirs()) {
                        throw IOException("Unable to create external directory: ${target.path}")
                    }
                    val children = source.listFiles()
                        ?: throw IOException("Unable to list staging directory: ${source.path}")
                    children.forEach { child ->
                        copyAndDelete(child, File(target, child.name))
                    }
                    if (source.list().isNullOrEmpty()) {
                        source.delete()
                    }
                    return
                }

                target.parentFile?.let { parent ->
                    if (!parent.isDirectory && !parent.mkdirs()) {
                        throw IOException("Unable to create external parent directory: ${parent.path}")
                    }
                }

                val temp = File(target.parentFile, target.name + ".gncopy")
                if (temp.exists()) temp.delete()

                source.inputStream().buffered(4 * 1024 * 1024).use { input ->
                    temp.outputStream().buffered(4 * 1024 * 1024).use { output ->
                        input.copyTo(output, 4 * 1024 * 1024)
                    }
                }

                if (temp.length() != source.length()) {
                    temp.delete()
                    throw IOException(
                        "External copy length mismatch: ${source.path} (${source.length()}) -> " +
                            "${target.path} (${temp.length()})"
                    )
                }

                if (target.exists() && !target.delete()) {
                    temp.delete()
                    throw IOException("Unable to replace external file: ${target.path}")
                }

                if (!temp.renameTo(target)) {
                    temp.copyTo(target, overwrite = true)
                    if (target.length() != source.length()) {
                        temp.delete()
                        throw IOException("External fallback copy failed: ${target.path}")
                    }
                    temp.delete()
                }

                target.setLastModified(source.lastModified())
                if (!source.delete()) {
                    throw IOException("Unable to remove staged file after transfer: ${source.path}")
                }
            }

            sourceRoot.listFiles()?.forEach { child ->
                // Keep the native resume journal on internal storage between depot runs.
                if (child.name != ".DepotDownloader") {
                    copyAndDelete(child, File(targetRoot, child.name))
                }
            }
        }

        fun downloadApp(
            appId: Int,
'''
if helper_anchor not in t:
    raise SystemExit('downloadApp helper anchor not found')
t = t.replace(helper_anchor, helper, 1)

old_download = '''                        Timber.i("Downloading game to " + defaultAppInstallPath)

                        GameDownloadService.downloadSteamApp(
                            appId = appId,
                            selectedDepots = selectedDepots,
                            branch = branch,
                            branchPassword = branchPassword,
                            installDir = getAppDirPath(appId),
                            isUpdateOrVerify = isUpdateOrVerify,
                            depotIdToIndex = depotIdToIndex,
                            downloadInfo = di,
                            // Adaptive-window ceiling (ramps up only while the link delivers);
                            // process pool stays core-scaled.
                            maxWorkers = speedConfig.maxDownloads,
                            processWorkers = speedConfig.maxDecompress,
                            parentScope = this,
                        )
'''
new_download = '''                        val useInternalStaging = PrefManager.useExternalStorage && !isUpdateOrVerify
                        val stagePath = if (useInternalStaging) {
                            Paths.get(internalAppInstallPath, ".gn_stage_$appId").pathString
                        } else {
                            appDirPath
                        }

                        Timber.i(
                            if (useInternalStaging) {
                                "Downloading Steam depots to internal staging $stagePath then flushing to $appDirPath"
                            } else {
                                "Downloading game to $appDirPath"
                            }
                        )

                        if (useInternalStaging) {
                            val stageDir = File(stagePath)
                            if (!stageDir.isDirectory && !stageDir.mkdirs()) {
                                throw IOException("Unable to create internal staging directory: $stagePath")
                            }
                            val targetDir = File(appDirPath)
                            if (!targetDir.isDirectory && !targetDir.mkdirs()) {
                                throw IOException("Unable to create external install directory: $appDirPath")
                            }

                            selectedDepots.toSortedMap().forEach { (depotId, depot) ->
                                val doneMarker = File(targetDir, ".gn_stage_done_$depotId")
                                val index = depotIdToIndex[depotId] ?: 0

                                if (doneMarker.isFile) {
                                    Timber.i("Skipping already staged depot $depotId")
                                    di.setProgress(1f, index)
                                    return@forEach
                                }

                                Timber.i("Downloading depot $depotId into internal staging")
                                GameDownloadService.downloadSteamApp(
                                    appId = appId,
                                    selectedDepots = mapOf(depotId to depot),
                                    branch = branch,
                                    branchPassword = branchPassword,
                                    installDir = stagePath,
                                    isUpdateOrVerify = false,
                                    depotIdToIndex = mapOf(depotId to index),
                                    downloadInfo = di,
                                    maxWorkers = speedConfig.maxDownloads,
                                    processWorkers = speedConfig.maxDecompress,
                                    parentScope = this,
                                )

                                Timber.i("Flushing completed depot $depotId to external storage")
                                flushStagedInstallToExternal(stagePath, appDirPath)
                                doneMarker.writeText("ok")
                                di.persistProgressSnapshot()
                            }

                            // The only thing intentionally left behind between depot flushes is
                            // the native journal. The whole staging tree can go after all depots.
                            if (stageDir.exists()) {
                                NativeTreeDelete.deleteTreeFast(stageDir)
                            }
                        } else {
                            GameDownloadService.downloadSteamApp(
                                appId = appId,
                                selectedDepots = selectedDepots,
                                branch = branch,
                                branchPassword = branchPassword,
                                installDir = appDirPath,
                                isUpdateOrVerify = isUpdateOrVerify,
                                depotIdToIndex = depotIdToIndex,
                                downloadInfo = di,
                                // Adaptive-window ceiling (ramps up only while the link delivers);
                                // process pool stays core-scaled.
                                maxWorkers = speedConfig.maxDownloads,
                                processWorkers = speedConfig.maxDecompress,
                                parentScope = this,
                            )
                        }
'''
if old_download not in t:
    raise SystemExit('Steam download block not found')
t = t.replace(old_download, new_download, 1)

cleanup_anchor = '''                        // Remove the job here — Play button becomes visible after this
                        removeDownloadJob(appId)
'''
cleanup = '''                        // Staging restart markers are only needed until the app is fully committed.
                        if (PrefManager.useExternalStorage && !isUpdateOrVerify) {
                            File(appDirPath).listFiles { _, name -> name.startsWith(".gn_stage_done_") }
                                ?.forEach { it.delete() }
                        }

                        // Remove the job here — Play button becomes visible after this
                        removeDownloadJob(appId)
'''
if cleanup_anchor not in t:
    raise SystemExit('cleanup anchor not found')
t = t.replace(cleanup_anchor, cleanup, 1)
write(steam_rel, t)

# Re-label the already-patched V6 debug build as V7.
gradle_rel = 'app/build.gradle.kts'
t = read(gradle_rel)
if '.usbfixv6' not in t or '-usbfixv6' not in t:
    raise SystemExit('V6 application id/version suffix not found')
t = t.replace('.usbfixv6', '.usbfixv7', 1)
t = t.replace('-usbfixv6', '-usbfixv7', 1)
write(gradle_rel, t)

strings_rel = 'app/src/main/res/values/strings.xml'
t = read(strings_rel)
old_name = '<string name="app_name">GameNative USB V6</string>'
new_name = '<string name="app_name">GameNative USB V7</string>'
if old_name not in t:
    raise SystemExit('V6 app_name not found')
write(strings_rel, t.replace(old_name, new_name, 1))

print('USB V7 staging patch applied: per-depot internal download -> Java stream flush to external')
