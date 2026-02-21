import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import CommandCenter from './pages/CommandCenter';
import Timeline from './pages/Timeline';
import Positions from './pages/Positions';
import SettingsPage from './pages/SettingsPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<CommandCenter />} />
          <Route path="timeline" element={<Timeline />} />
          <Route path="positions" element={<Positions />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
