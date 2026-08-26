/**
 * Prism Motion Graphics Engine — Headless Electron/Chromium Frame Runner
 * ──────────────────────────────────────────────────────────────────────
 * Steps frame-by-frame through the Motion Runtime canvas and streams MJPEG
 * buffers directly to stdout for FFmpeg image2pipe encoding.
 */
const { app, BrowserWindow } = require("electron");
const path = require("path");
const fs = require("fs");

app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-software-rasterizer");
app.commandLine.appendSwitch("no-sandbox");

const specFile = process.argv[2];
if (!specFile || !fs.existsSync(specFile)) {
  process.stderr.write(`ERROR: Specification file not found: ${specFile}\n`);
  app.exit(1);
}

const specData = JSON.parse(fs.readFileSync(specFile, "utf-8"));
const width = (specData.project && specData.project.width) || 1080;
const height = (specData.project && specData.project.height) || 1920;
const fps = (specData.project && specData.project.fps) || 30;
const duration = (specData.project && specData.project.duration) || 10.0;
const totalFrames = Math.round(duration * fps);

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: width,
    height: height,
    show: false,
    useContentSize: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: false,
      offscreen: true
    }
  });

  const indexPath = path.join(__dirname, "index.html");
  await win.loadFile(indexPath);

  await win.webContents.executeJavaScript(`window.__loadSpec(${JSON.stringify(specData)})`);

  for (let frame = 0; frame < totalFrames; frame++) {
    await win.webContents.executeJavaScript(`window.__seek(${frame})`);

    const image = await win.webContents.capturePage({ x: 0, y: 0, width, height });
    const jpegBuffer = image.toJPEG(92);

    process.stdout.write(jpegBuffer);

    if (process.send) {
      process.send({ frame: frame + 1, total: totalFrames });
    }
  }

  app.exit(0);
});
