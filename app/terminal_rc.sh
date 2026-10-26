# MatiPanel — inicio de la terminal web (bash interactivo, estilo Linux Lite).
# Carga el entorno normal del usuario y agrega banner de bienvenida + prompt por segmentos.

[ -r /etc/profile ] && . /etc/profile
if [ -r "$HOME/.profile" ]; then . "$HOME/.profile"      # en Debian ya incluye ~/.bashrc
elif [ -r "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi

# ── Banner de bienvenida ──
__mp_banner() {
  local os mem disk load up
  os=$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")
  mem=$(free -m 2>/dev/null | awk '/^Mem:/ {printf "%d/%d MB (%.1f%%)", $3, $2, $3*100/$2}')
  disk=$(df -h / 2>/dev/null | awk 'NR==2 {printf "%s/%s (%s)", $3, $2, $5}')
  load=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)
  up=$(uptime -p 2>/dev/null | sed 's/^up //')
  printf '\e[1;38;5;79mBienvenido a %s\e[0m \e[1m%s\e[0m\n\n' "${os:-Linux}" "$USER"
  printf '%s\n' "$(LC_TIME=es_ES.UTF-8 date '+%A %d %B %Y, %H:%M:%S' 2>/dev/null || date)"
  printf 'Uso de memoria: %s\n' "${mem:-?}"
  printf 'Uso de disco:   %s\n' "${disk:-?}"
  printf 'Carga:          %s  ·  encendido %s\n' "${load:-?}" "${up:-?}"
  printf '\e[2mMatiPanel · Ctrl+C interrumpe · Ctrl+D cierra la sesión\e[0m\n\n'
}
__mp_banner

# ── Prompt por segmentos: [usuario] [ruta › con › separadores] › ──
# La ruta va en variables referenciadas desde PS1 (comillas simples): así un directorio
# llamado '$(comando)' se muestra tal cual y nunca se ejecuta.
__mp_prompt() {
  local st=$? p=${PWD/#$HOME/\~}
  local disp=${p//\// › }
  [[ $p == /* ]] && disp="/${disp# › }"
  if [[ $disp == *' › '* ]]; then
    __mp_head="${disp% › *} › "; __mp_last=${disp##* › }
  else
    __mp_head=""; __mp_last=$disp
  fi
  __mp_st=""; [[ $st -ne 0 ]] && __mp_st=" $st "
}
PROMPT_COMMAND="__mp_prompt${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
if [[ $EUID -eq 0 ]]; then __mp_u=160; else __mp_u=30; fi
PS1='\[\e[1;48;5;${__mp_u};38;5;231m\] \u \[\e[0;48;5;238;38;5;250m\] ${__mp_head}\[\e[1;38;5;255m\]${__mp_last} \[\e[0;48;5;160;38;5;231m\]${__mp_st}\[\e[0;38;5;${__mp_u}m\] ›\[\e[0m\] '
