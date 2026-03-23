const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('overlay', {
  onStateChange: (callback) => ipcRenderer.on('overlay-state', (_, state) => callback(state)),
  toggleClickThrough: () => ipcRenderer.send('toggle-click-through'),
});
