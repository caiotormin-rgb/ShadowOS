# ~/.local/bin on PATH (gh CLI and other userspace tools live here)
# Tracked in the workspace repo; edit here, not in ~/.bashrc.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
