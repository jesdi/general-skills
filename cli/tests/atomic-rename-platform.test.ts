import { afterEach, describe, expect, it, vi } from 'vitest';

const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
afterEach(() => {
  Object.defineProperty(process, 'platform', platform);
  vi.doUnmock('koffi');
  vi.resetModules();
});

async function nativeFixture(os: string, result: number, error: number) {
  Object.defineProperty(process, 'platform', { ...platform, value: os });
  vi.resetModules();
  const call = vi.fn(() => result);
  const load = vi.fn(() => ({ func: (signature: string) => signature.includes('GetLastError') ? () => error : call }));
  vi.doMock('koffi', () => ({ default: { load, errno: () => error, os: { errno: { EEXIST: 17, ENOENT: 2 } } } }));
  const { renameNoReplace } = await import('../src/atomic-rename.js');
  return { renameNoReplace, call, load };
}

describe('atomic move platform contracts', () => {
  it.each([
    ['darwin', '/usr/lib/libSystem.B.dylib', -2, 4],
    ['linux', null, -100, 1],
  ])('uses the exclusive syscall on %s', async (os, library, directory, flag) => {
    const f = await nativeFixture(String(os), 0, 0);
    await f.renameNoReplace('source', 'destination');
    expect(f.load).toHaveBeenCalledWith(library);
    expect(f.call).toHaveBeenCalledWith(directory, 'source', directory, 'destination', flag);
  });

  it.each([[17, 'EEXIST'], [2, 'ENOENT'], [95, 'EIO']])('reports POSIX error %s without a fallback move', async (errno, code) => {
    const f = await nativeFixture('linux', -1, Number(errno));
    await expect(f.renameNoReplace('source', 'destination')).rejects.toMatchObject({ code });
    expect(f.call).toHaveBeenCalledTimes(1);
  });

  it('publishes on Windows with neither replacement nor copy flags', async () => {
    const f = await nativeFixture('win32', 1, 0);
    await f.renameNoReplace('source', 'destination');
    expect(f.call).toHaveBeenCalledWith('source', 'destination', 0);
  });

  it.each([[80, 'EEXIST'], [183, 'EEXIST'], [2, 'ENOENT'], [3, 'ENOENT'], [5, 'EIO']])(
    'reports Windows error %s without a fallback move', async (error, code) => {
      const f = await nativeFixture('win32', 0, Number(error));
      await expect(f.renameNoReplace('source', 'destination')).rejects.toMatchObject({ code });
      expect(f.call).toHaveBeenCalledTimes(1);
    },
  );

  it('rejects unsupported platforms before loading a native library', async () => {
    const f = await nativeFixture('freebsd', 0, 0);
    await expect(f.renameNoReplace('source', 'destination')).rejects.toThrow('unsupported on freebsd');
    expect(f.load).not.toHaveBeenCalled();
  });
});
