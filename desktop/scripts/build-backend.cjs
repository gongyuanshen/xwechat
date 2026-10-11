"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { aiPackagingArgs } = require("./ai-packaging.cjs");

const repoRoot = path.resolve(__dirname, "..", "..");
const entry = path.join(repoRoot, "src", "wechat_decrypt_tool", "backend_entry.py");

const distDir = path.join(repoRoot, "desktop", "resources", "backend");
const workDir = path.join(repoRoot, "desktop", "build", "pyinstaller");
const specDir = path.join(repoRoot, "desktop", "build", "pyinstaller-spec");
const nativeDir = path.join(repoRoot, "src", "wechat_decrypt_tool", "native");
const skillDir = path.join(repoRoot, "skills", "wechat-mcp-copilot");

function pyInstallerAddData(sourcePath, targetPath) {
  return `${sourcePath}${path.delimiter}${targetPath}`;
}

function parseVersionTuple(rawVersion) {
  if (typeof rawVersion !== "string" || !/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(rawVersion)) {
    throw new Error("desktop/package.json version must use numeric major.minor.patch format");
  }
  const nums = rawVersion.split(".").map(Number);
  if (nums.some((value) => value > 65535)) {
    throw new Error("desktop/package.json version components must be between 0 and 65535 for Windows resources");
  }
  return [...nums, 0];
}

function buildVersionInfoText(versionTuple, versionDot) {
  const [a, b, c, d] = versionTuple;
  return `# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(${a}, ${b}, ${c}, ${d}),
    prodvers=(${a}, ${b}, ${c}, ${d}),
    mask=0x3f,
    flags=0x0,
    OS=0x4,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [StringStruct('CompanyName', 'xwechat'),
        StringStruct('FileDescription', 'xwechat Backend'),
        StringStruct('FileVersion', '${versionDot}'),
        StringStruct('InternalName', 'wechat-backend'),
        StringStruct('LegalCopyright', 'xwechat'),
        StringStruct('OriginalFilename', 'wechat-backend.exe'),
        StringStruct('ProductName', 'xwechat'),
        StringStruct('ProductVersion', '${versionDot}')])
      ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
`;
}

function main() {
  const desktopPackageJsonPath = path.join(repoRoot, "desktop", "package.json");
  const pkg = JSON.parse(fs.readFileSync(desktopPackageJsonPath, { encoding: "utf8" }));
  const versionTuple = parseVersionTuple(pkg.version);
  const versionDot = versionTuple.join(".");

  fs.mkdirSync(distDir, { recursive: true });
  fs.mkdirSync(workDir, { recursive: true });
  fs.mkdirSync(specDir, { recursive: true });

  const versionFilePath = path.join(workDir, "xwechat-backend-version.txt");
  if (process.platform === "win32") {
    fs.writeFileSync(versionFilePath, buildVersionInfoText(versionTuple, versionDot), { encoding: "utf8" });
  }

  const args = [
    "run",
    "pyinstaller",
    "--noconfirm",
    "--clean",
    "--name",
    "wechat-backend",
    "--onefile",
    "--distpath",
    distDir,
    "--workpath",
    workDir,
    "--specpath",
    specDir,
    "--add-data",
    pyInstallerAddData(path.join(nativeDir, "*.dll"), "wechat_decrypt_tool/native"),
    "--add-data",
    pyInstallerAddData(path.join(nativeDir, "weflow_wasm", "*.js"), "wechat_decrypt_tool/native/weflow_wasm"),
    "--add-data",
    pyInstallerAddData(path.join(nativeDir, "weflow_wasm", "*.wasm"), "wechat_decrypt_tool/native/weflow_wasm"),
    "--add-data",
    pyInstallerAddData(skillDir, "skills/wechat-mcp-copilot"),
    "--add-data",
    pyInstallerAddData(path.join(repoRoot, "src/wechat_decrypt_tool/resources"), "wechat_decrypt_tool/resources"),
    "--collect-all",
    "faster_whisper",
    "--collect-all",
    "ctranslate2",
    "--collect-all",
    "av",
    "--collect-all",
    "opencc",
    "--collect-all",
    "sherpa_onnx",
    "--collect-all",
    "watchfiles",
    ...aiPackagingArgs(),
    entry,
  ];

  if (process.argv.includes("--qwen-gpu")) {
    args.splice(args.length - 1, 0, "--collect-all", "torch", "--collect-all", "transformers");
  } else {
    args.splice(args.length - 1, 0, "--exclude-module", "torch", "--exclude-module", "transformers");
  }

  if (process.platform === "win32") {
    args.splice(
      args.length - 1,
      0,
      "--version-file",
      versionFilePath,
      "--icon",
      path.join(repoRoot, "desktop", "src", "icon.ico"),
      "--hidden-import",
      "wechat_decrypt_tool.key_v4",
      "--hidden-import",
      "yara",
      "--collect-all",
      "uiautomation"
    );
  }

  console.log("Running PyInstaller to build wechat-backend...");
  const result = spawnSync("uv", args, { cwd: repoRoot, stdio: "inherit", shell: true });
  if ((result.status ?? 1) !== 0) {
    throw new Error(`PyInstaller build failed with status ${result.status}`);
  }

  const packagedBackend = path.join(
    distDir,
    process.platform === "win32" ? "wechat-backend.exe" : "wechat-backend"
  );
  if (!fs.existsSync(packagedBackend)) {
    throw new Error(`Packaged backend executable not found at: ${packagedBackend}`);
  }

  console.log(`Backend built successfully: ${packagedBackend}`);

  // Copy native dependencies alongside backend for stable path resolution
  const packagedNativeDir = path.join(distDir, "native");
  fs.rmSync(packagedNativeDir, { recursive: true, force: true });
  fs.cpSync(nativeDir, packagedNativeDir, { recursive: true, force: true });
  console.log(`Copied native dependencies to: ${packagedNativeDir}`);

  // Smoke test the packaged backend
  console.log("Running packaged backend smoke test...");
  const smokeDataDir = path.join(workDir, "smoke-data");
  const smoke = spawnSync(packagedBackend, ["--smoke-backend"], {
    cwd: workDir,
    env: {
      ...process.env,
      WECHAT_TOOL_DATA_DIR: smokeDataDir,
      WECHAT_TOOL_OUTPUT_DIR: path.join(smokeDataDir, "output"),
    },
    encoding: "utf8",
    windowsHide: true,
    timeout: 30000,
  });
  if ((smoke.status ?? 1) !== 0) {
    throw new Error(`Packaged backend smoke test failed: ${smoke.stderr || smoke.stdout}`);
  }
  console.log(`Packaged backend smoke test passed: ${smoke.stdout.trim()}`);
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(error?.message || error);
    process.exit(1);
  }
}

module.exports = { main };
