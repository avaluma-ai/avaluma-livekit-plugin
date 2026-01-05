import asyncio
import logging

from livekit import api, rtc
from livekit.protocol import ingress as proto_ingress
from livekit.protocol.room import ListParticipantsRequest, UpdateSubscriptionsRequest

logger = logging.getLogger(__name__)


async def mute_track_for_user(track: rtc.Track, room: rtc.Room):
    await asyncio.sleep(0.5)  # TODO könnte wichtig sein

    lkapi = api.LiveKitAPI()
    room_service = lkapi.room

    participants_in_room = await room_service.list_participants(
        ListParticipantsRequest(room=room.name)
    )
    print("Participant in Room")
    for participant in participants_in_room.participants:
        print(f"Participant: {participant.identity}")
        if participant.kind == api.ParticipantInfo.Kind.STANDARD:
            print(f"Update Participant: {participant.identity}")
            await room_service.update_subscriptions(
                update=UpdateSubscriptionsRequest(
                    room=room.name,
                    identity=participant.identity,
                    track_sids=[track.sid],
                    subscribe=False,
                )
            )


async def delete_all_ingress_for_room(room: rtc.Room) -> None:
    """Delete all ingress endpoints for this room during shutdown."""
    logger.info("Deleting ingress endpoints...")

    lkapi = api.LiveKitAPI()

    try:
        # List ingress endpoints
        resp = await lkapi.ingress.list_ingress(
            proto_ingress.ListIngressRequest(room_name=room.name)
        )
        logger.info(f"Found {len(resp.items)} ingress endpoints to delete")

        deleted = 0
        for info in resp.items:
            await lkapi.ingress.delete_ingress(
                proto_ingress.DeleteIngressRequest(ingress_id=info.ingress_id)
            )
            logger.info(f"Deleted ingress endpoint {info.ingress_id}")
            deleted += 1

        logger.info(
            f"Successfully deleted {deleted} ingress endpoints for room {room.name}"
        )
    finally:
        await lkapi.aclose()
