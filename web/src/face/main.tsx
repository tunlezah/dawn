import { createRoot } from 'react-dom/client';
import '../shared/theme.css';
import './face.css';
import { Face } from './Face';
import { store } from '../shared/store';

store.start('face');
createRoot(document.getElementById('root')!).render(<Face />);
