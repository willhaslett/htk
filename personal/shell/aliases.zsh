# Optional interactive convenience; source this file from your shell configuration.
# Prefer HTK's Codex launcher while keeping the real Codex installation on PATH.
typeset -U path
path=("$HOME/.local/bin" $path)
alias status='status-list'
alias hconfig='${EDITOR:-nvim} "$HOME/.config/herdr/config.toml"'
alias gconfig='${EDITOR:-nvim} "$HOME/.config/ghostty/config"'
alias hreload='herdr server reload-config'
