// Names the weigh dashboard test deletes from a spread copy of process.env; the spread drops the index signature, so `delete` needs them declared optional.
declare namespace NodeJS {
  interface ProcessEnv {
    VIRTUAL_ENV?: string;
    PYTHONPATH?: string;
  }
}
