# Avaluma-Livekit-Plugin

## Installation

Install with pip from git:

```bash
pip install git+https://github.com/avaluma-ai/avaluma-livekit-plugin.git

# git clone https://github.com/avaluma-ai/avaluma-livekit-plugin.git
# pip install path/to/avaluma-livekit-plugin
```

## Usage/Example

1. Start with the [agent-starter-python](https://github.com/livekit-examples/agent-starter-python) project from LiveKit.
2. Install this plugin
3. According to the [Guide for adding virtual avatars to livekit agents](https://docs.livekit.io/agents/models/avatar/) add the following to the `agent.py` file:

    - add imports
      ```python
        from avaluma_livekit_plugin import AvatarSession
        from livekit.agents import RoomOutputOptions
      ```
      
    - search for `await session.start` and replace it with  
  
      ```python
      avatar = AvatarSession(
          license_key="YOUR_LICENSE_KEY",
          avatar_id="YOUR_AVATAR_ID",
          avatar_server_url="YOUR_AVATAR_SERVER_URL"
      )
      await avatar.start(session, room=ctx.room)
  
      # Start the session, which initializes the voice pipeline and warms up the models
      await session.start(
          agent=Assistant(),
          room=ctx.room,
          room_input_options=RoomInputOptions(
              # For telephony applications, use `BVCTelephony` for best results
              noise_cancellation=noise_cancellation.BVC(),
          ),
          room_output_options=RoomOutputOptions(audio_enabled=False),
      )
      ```
4. start agent with `uv run python agent.py dev`
