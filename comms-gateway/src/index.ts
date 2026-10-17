/** Gateway entry point. `npm run dev` / `npm start`. */

import { hasPhotonCreds, loadConfig } from "./config.js";
import { PhotonGateway } from "./photon.js";
import { createServer, startInboundPoller } from "./server.js";

const cfg = loadConfig();
const gateway = new PhotonGateway(cfg);
const server = createServer(cfg, gateway);

server.listen(cfg.port, () => {
  const mode = cfg.stubPhoton ? "STUB (no Photon, outbox only)" : "LIVE (Photon)";
  console.log(`comms-gateway listening on :${cfg.port} — ${mode}`);
  console.log(`forwarding inbound to ${cfg.backendUrl}/comms/inbound`);

  if (!cfg.sharedSecret) {
    console.error(
      "SHARED_SECRET is not set; inbound forwards go out unsigned and the backend " +
        "only accepts them while its STUB_PHOTON=1. Set it in .env for the live path.",
    );
  }
  if (!cfg.stubPhoton) {
    if (!hasPhotonCreds(cfg)) {
      console.error(
        "STUB_PHOTON=0 but PHOTON_TOKEN / PHOTON_PROJECT_ID / PHOTON_AGENT_HANDLE " +
          "are not all set. Fill them in .env or keep STUB_PHOTON=1.",
      );
    } else {
      startInboundPoller(cfg, gateway);
      console.log("inbound poller started (polling Photon every 2s)");
    }
  }
});
