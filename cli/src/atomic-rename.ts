let move: ((source: string, destination: string) => void) | undefined;
type NativeLibrary = { func: (signature: string) => (...args: unknown[]) => number };
type NativeBinding = Pick<typeof import('koffi')['default'], 'load' | 'errno' | 'os'>;

/** Windows exposes its own error values rather than POSIX errno. */
function windowsMove(lib: NativeLibrary): (source: string, destination: string) => void {
  const rename = lib.func('int __stdcall MoveFileExW(const char16_t *source, const char16_t *destination, uint32_t flags)');
  const lastError = lib.func('uint32_t __stdcall GetLastError()');
  return (source, destination) => {
    // No MOVEFILE_REPLACE_EXISTING or cross-volume copy fallback.
    if (rename(source, destination, 0)) return;
    const error = lastError();
    const code = error === 80 || error === 183 ? 'EEXIST' : error === 2 || error === 3 ? 'ENOENT' : 'EIO';
    throw Object.assign(new Error(`exclusive rename ${source} -> ${destination} failed: Windows error ${error}`), { code });
  };
}

function posixMove(koffi: NativeBinding, darwin: boolean): (source: string, destination: string) => void {
  const lib = koffi.load(darwin ? '/usr/lib/libSystem.B.dylib' : null);
  const rename = lib.func(`int ${darwin ? 'renameatx_np' : 'renameat2'}(int, const char *, int, const char *, unsigned int)`);
  const currentDirectory = darwin ? -2 : -100;
  const exclusive = darwin ? 4 : 1; // RENAME_EXCL / RENAME_NOREPLACE
  return (source, destination) => {
    if (rename(currentDirectory, source, currentDirectory, destination, exclusive) === 0) return;
    const errno = koffi.errno();
    const code = Object.entries(koffi.os.errno).find(([, value]) => value === errno)?.[0] ?? 'EIO';
    throw Object.assign(new Error(`exclusive rename ${source} -> ${destination} failed: ${code}`), { code });
  };
}

/** Load the OS operation lazily so ordinary CLI commands need no native binding. */
async function loadMove(): Promise<(source: string, destination: string) => void> {
  const { default: koffi } = await import('koffi');
  if (process.platform === 'win32') {
    return windowsMove(koffi.load('kernel32.dll'));
  }
  if (process.platform !== 'darwin' && process.platform !== 'linux') {
    throw new Error(`atomic external skill replacement is unsupported on ${process.platform}`);
  }
  return posixMove(koffi, process.platform === 'darwin');
}

/** Atomic no-replace publication and rollback. Unsupported filesystems fail closed. */
export async function renameNoReplace(source: string, destination: string): Promise<void> {
  move ??= await loadMove();
  move(source, destination);
}
