# Avaluma-Livekit-Plugin

Avaluma avatar plugin for [LiveKit Agents](https://docs.livekit.io/agents/).

## Installation

Install with pip from git:

```bash
pip install git+https://github.com/avaluma-ai/avaluma-livekit-plugin.git

# Or from a local clone:
# git clone https://github.com/avaluma-ai/avaluma-livekit-plugin.git
# pip install path/to/avaluma-livekit-plugin
```

## Pre-requisites

The plugin needs your Avaluma credentials and a LiveKit connection. These can be
passed as arguments or set as environment variables:

```bash
export AVALUMA_API_KEY=<your-avaluma-license-key>
export AVALUMA_AVATAR_ID=<your-avatar-id>
# optional, defaults to https://api.avaluma.ai
export AVALUMA_API_URL=<your-avatar-server-url>

# LiveKit connection (usually already set in your agent project)
export LIVEKIT_URL=<your-livekit-url>
export LIVEKIT_API_KEY=<your-livekit-api-key>
export LIVEKIT_API_SECRET=<your-livekit-api-secret>
```

## Usage/Example

1. Start with the [agent-starter-python](https://github.com/livekit-examples/agent-starter-python) project from LiveKit.
2. Install this plugin.
3. Following the [guide for adding virtual avatars to LiveKit agents](https://docs.livekit.io/agents/models/avatar/), add the following to your `agent.py`:

    - add imports
      ```python
      from avaluma_livekit_plugin import AvatarSession
      from livekit.agents import RoomInputOptions, RoomOutputOptions
      ```

    - search for `await session.start` and replace it with

      ```python
      # Credentials can also be provided via the AVALUMA_API_KEY /
      # AVALUMA_AVATAR_ID environment variables.
      avatar = AvatarSession(
          license_key="YOUR_LICENSE_KEY",
          avatar_id="YOUR_AVATAR_ID",
          # avatar_server_url="YOUR_AVATAR_SERVER_URL",  # optional
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
          # the avatar publishes the audio track, so disable the room audio output
          room_output_options=RoomOutputOptions(audio_enabled=False),
      )
      ```
4. start agent with `uv run python agent.py dev`
