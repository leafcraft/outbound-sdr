"""outbound-sdr — Outbound SDR operation entry point.

Boots the mesh, registers the WORM audit sink, and imports the tools
module so its @global_tool functions register at startup. See PRD.md for
the full design.
"""
import asyncio
import signal
import sys

from dotenv import load_dotenv
from leafmesh import EventType, LeafMesh, LeafMeshLogger

from agency._shared import audit_logger as _audit_logger
import agency.tools.tools  # noqa: F401 — registers @global_tool functions

load_dotenv()
logger = LeafMeshLogger(__name__)

leafmesh = LeafMesh.from_store()
_audit_logger.register(leafmesh, "audit_logger_agent")


async def _on_agent_error(e):          logger.warning(f"[sdr] AGENT_ERROR {e.session_id}")
async def _on_manager_intervention(e): logger.warning(f"[sdr] MANAGER_INTERVENTION {e.session_id}")
async def _on_intervention_needed(e):  logger.error(f"[sdr] INTERVENTION_NEEDED {e.session_id}")
async def _on_human_input_timeout(e):  logger.warning(f"[sdr] HITL TIMEOUT {e.session_id}")


async def main():
    await leafmesh.start()
    leafmesh.event_bus.subscribe(EventType.AGENT_ERROR,          _on_agent_error)
    leafmesh.event_bus.subscribe(EventType.MANAGER_INTERVENTION, _on_manager_intervention)
    leafmesh.event_bus.subscribe(EventType.INTERVENTION_NEEDED,  _on_intervention_needed)
    leafmesh.event_bus.subscribe(EventType.HUMAN_INPUT_TIMEOUT,  _on_human_input_timeout)
    # Self-healing is automatic — the SDK enables it during start() on
    # every operator-facing mesh. No call is needed.
    logger.info("outbound-sdr — Outbound SDR mesh running")
    logger.info("API docs: http://127.0.0.1:18820/docs")

    # ── Onboard a company to start the operation (uncomment to demo) ──
    # result = await leafmesh.mesh_call("onboard_company", input_data={
    #     "company_name": "Acme Robotics",
    #     "website": "https://acme.example",
    #     "linkedin": "https://linkedin.com/company/acme",
    # })
    # logger.info(f"GTM: {result}")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    else:
        try:
            await stop.wait()
        except KeyboardInterrupt:
            pass
    await leafmesh.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
