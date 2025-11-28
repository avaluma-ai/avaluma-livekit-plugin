# Avaluma-Livekit-Plugin

## Installation

Install with pip from git:

```bash
pip install git+https://github.com/avaluma-ai/avaluma-livekit-plugin.git

# git clone https://github.com/avaluma-ai/avaluma-livekit-plugin.git
# pip install path/to/avaluma-livekit-plugin
```

### For Local Run only

`avaluma_runtime.cpython-312-x86_64-linux-gnu` and `lib` have to be copy to the `bin` folder of the installed plugin. Please check the logs of the first run to find the path of the `bin` folder.

It should be like this: `.venv/lib/python3.12/site-packages/avaluma_livekit_plugin/local/bin`

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
      # Local Avatar
      avatar = AvatarSession(
          license_key="YOUR_LICENSE_KEY",
          avatar_id="YOUR_AVATAR_ID",
          assets_dir="YOUR_ASSETS_DIR", # with ASSETS_DIR/AVATAR_ID.hvia
          mode="local"
      )
      # Remote Avatar
      # avatar = AvatarSession(
      #     license_key="YOUR_LICENSE_KEY",
      #     avatar_id="YOUR_AVATAR_ID",  # See https://docs.livekit.io/agents/models/avatar/plugins/avaluma
      #     avatar_server_url="https://api.avaluma.ai", # with AVATAR_ID.hvia on server
      #     mode="remote"
      # )
      # Start the avatar and wait for it to join
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
