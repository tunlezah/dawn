import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../shared/theme.css';
import { App } from './App';
import { store } from '../shared/store';

store.start('control');
if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
