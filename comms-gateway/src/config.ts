/** Gateway config, read from the shared root .env (see .env.example). */

export interface Config {
  port: number;
  stubPhoton: boolean;
  backendUrl: string; // where inbound caregiver replies are forwarded
  photon: {
    token: string; // PHOTON_TOKEN  (project API key, pho_sk_...)
    projectId: string; // PHOTON_PROJECT_ID (pho_prj_...)
    agentHandle: string; // PHOTON_AGENT_HANDLE (the assigned line, E.164)
    baseUrl: string; // PHOTON_SERVER_ADDRESS, default api.photon.codes/v1
  };
}

function env(name: string, fallback = ""): string {
  return process.env[name]?.trim() ?? fallback;
}

export function loadConfig(): Config {
  return {
    port: Number(env("COMMS_GATEWAY_PORT", "8787")),
    // STUB_PHOTON=1 (default) => no SDK, no creds, no network.
    stubPhoton: env("STUB_PHOTON", "1") !== "0",
    backendUrl: env("BACKEND_URL", "http://localhost:8000"),
    photon: {
      token: env("PHOTON_TOKEN"),
      projectId: env("PHOTON_PROJECT_ID"),
      agentHandle: env("PHOTON_AGENT_HANDLE"),
      baseUrl: env("PHOTON_SERVER_ADDRESS", "https://api.photon.codes/v1"),
    },
  };
}

/** True when we have everything needed to talk to Photon for real. */
export function hasPhotonCreds(c: Config): boolean {
  return Boolean(c.photon.token && c.photon.projectId && c.photon.agentHandle);
}
