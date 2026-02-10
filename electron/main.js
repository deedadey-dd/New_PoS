const { app, BrowserWindow, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const waitOn = require('wait-on');

let mainWindow;
let djangoProcess;

const isDev = !app.isPackaged;
const PORT = 8000;
const BASE_URL = `http://127.0.0.1:${PORT}`;

function getPythonDetails() {
    if (isDev) {
        return {
            executable: path.join(__dirname, '..', 'venv', 'Scripts', 'python.exe'),
            script: path.join(__dirname, '..', 'manage.py'),
            cwd: path.join(__dirname, '..')
        };
    } else {
        // Production: resources/venv/... and resources/app/... (or flattened)
        // Adjust based on how electron-builder packages files
        return {
            executable: path.join(process.resourcesPath, 'venv', 'Scripts', 'python.exe'), // Bundled venv
            script: path.join(process.resourcesPath, 'manage.py'), // If manage.py is at resources root
            cwd: process.resourcesPath
        };
    }
}

function startDjango() {
    const { executable, script, cwd } = getPythonDetails();
    console.log(`Starting Django: ${executable} ${script}`);

    djangoProcess = spawn(executable, [script, 'runserver', PORT.toString(), '--noreload'], {
        cwd: cwd
    });

    djangoProcess.stdout.on('data', (data) => {
        console.log(`Django stdout: ${data}`);
    });

    djangoProcess.stderr.on('data', (data) => {
        console.error(`Django stderr: ${data}`);
    });

    djangoProcess.on('close', (code) => {
        console.log(`Django process exited with code ${code}`);
    });
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1200,
        height: 900,
        icon: path.join(__dirname, '../static/images/logo.png'),
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js') // Optional if we need it
        }
    });

    mainWindow.loadURL(BASE_URL);

    // Filter navigation
    mainWindow.webContents.on('will-navigate', (event, url) => {
        const parsedUrl = new URL(url);
        if (parsedUrl.hostname !== '127.0.0.1' && parsedUrl.hostname !== 'localhost') {
            event.preventDefault();
            require('electron').shell.openExternal(url);
        }
    });

    mainWindow.on('closed', function () {
        mainWindow = null;
    });
}

app.on('ready', () => {
    startDjango();

    // Wait for server
    waitOn({
        resources: [BASE_URL],
        timeout: 20000 // 20s
    }).then(() => {
        createWindow();
    }).catch((err) => {
        console.error('Django did not start in time', err);
        dialog.showErrorBox('Startup Error', 'Could not start the application server.');
        app.quit();
    });
});

app.on('window-all-closed', function () {
    if (process.platform !== 'darwin') {
        app.quit();
    }
});

app.on('activate', function () {
    if (mainWindow === null) {
        createWindow();
    }
});

app.on('will-quit', () => {
    if (djangoProcess) {
        djangoProcess.kill();
    }
});
