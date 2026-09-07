import { describe, expect, it, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { Transparency } from '../screens/Transparency';
import { AppStoreProvider } from '../lib/store';
import { isWebGLAvailable } from '../lib/mapcore';

// jsdom has no WebGL → map components must fail soft and never crash.
describe('transparency map tab (no WebGL)', () => {
  beforeEach(() => window.localStorage.clear());

  it('offers a Map tab that degrades gracefully without WebGL', async () => {
    expect(isWebGLAvailable()).toBe(false);
    render(
      <AppStoreProvider>
        <Transparency stateId="lagos" />
      </AppStoreProvider>,
    );
    fireEvent.click(screen.getByRole('tab', { name: /^map$/i }));
    await waitFor(() =>
      expect(screen.getByRole('tabpanel', { name: /projects by lga map/i })).toHaveTextContent(
        /map view unavailable/i,
      ),
    );
    expect(screen.queryByTestId('projects-map')).not.toBeInTheDocument();
  });
});
