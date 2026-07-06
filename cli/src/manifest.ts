export interface ManifestSkill {
  name: string;
  version: string;
  hash: string;
  description: string;
}

export interface Manifest {
  schemaVersion: 1;
  generatedAt: string;
  skills: ManifestSkill[];
}
