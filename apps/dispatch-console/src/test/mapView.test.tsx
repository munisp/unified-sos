import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Cadastre3D } from '../components/Cadastre3D';
import { MapView } from '../components/MapView';
import { ConsoleProvider } from '../lib/store';

function renderMap() {
  return render(
    <ConsoleProvider>
      <MapView />
    </ConsoleProvider>,
  );
}

describe('MapView', () => {
  it('fails soft to the schematic SVG panel when WebGL is unavailable (jsdom)', async () => {
    renderMap();
    // jsdom canvas has no WebGL → MapLibrePanel reports the error and the
    // view switches to the schematic fallback automatically.
    await waitFor(() => expect(screen.getByTestId('schematic-map')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent(/schematic fallback/i);
  });

  it('keeps a manual schematic toggle', async () => {
    renderMap();
    const btn = await screen.findByRole('button', { name: 'Schematic' });
    fireEvent.click(btn);
    expect(screen.getByTestId('schematic-map')).toBeInTheDocument();
  });
});

describe('Cadastre3D gating', () => {
  it('renders nothing when VITE_ENABLE_3D is unset', () => {
    const { container } = render(
      <ConsoleProvider>
        <Cadastre3D />
      </ConsoleProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
