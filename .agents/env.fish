# .agents/env.fish — Tier 2 environment contract for fish.
#
# fish is not POSIX, so it cannot source .agents/env.sh. This file deliberately
# contains NO logic of its own: it asks the real contract to print the
# environment it manages (`bash .agents/env.sh --dump`) and adopts it, then
# defines the `qmd` / `qmd-cpu` wrappers by delegating to the Tier 1 hermetic
# entrypoints. There is nothing here to keep in sync.
#
# Usage:  source /path/to/repo/.agents/env.fish
#
# As always, Tier 2 is ergonomics only. Every .agents/bin/* entrypoint carries a
# bash shebang and is therefore already correct under fish with no setup at all.

set -l _ttrpg_self (status filename)
if test -z "$_ttrpg_self"
    echo ".agents/env.fish must be sourced, not piped." >&2
else
    set -l _ttrpg_root (realpath (dirname $_ttrpg_self)/..)
    set -gx TTRPG_ROOT $_ttrpg_root

    # One bash subprocess computes everything; we only transcribe the result.
    # Failing silently here would leave the session pointed at ~/.cache with no
    # warning, so check before consuming.
    set -l _ttrpg_dump (env TTRPG_ROOT=$_ttrpg_root TTRPG_ENV_DUMP=1 bash $_ttrpg_root/.agents/env.sh 2>&1)
    if test $status -ne 0 -o (count $_ttrpg_dump) -lt 2
        echo "ttrpg env.fish: could not read the environment contract from bash." >&2
        printf '%s\n' $_ttrpg_dump >&2
        echo "Tools still work via $_ttrpg_root/.agents/bin/*; a bare `qmd` will not be project-local." >&2
    end
    for _ttrpg_line in $_ttrpg_dump
        set -l _ttrpg_kv (string split -m 1 '=' -- $_ttrpg_line)
        test (count $_ttrpg_kv) -eq 2; or continue
        set -l _ttrpg_key $_ttrpg_kv[1]
        set -l _ttrpg_val $_ttrpg_kv[2]

        if test "$_ttrpg_key" = PATH
            # PATH is a list in fish; splitting keeps `fish_add_path` and friends working.
            set -gx PATH (string split ':' -- $_ttrpg_val)
        else
            set -gx $_ttrpg_key $_ttrpg_val
        end
    end
    set -e _ttrpg_line
    set -e _ttrpg_kv

    # env.sh's qmd()/qmd-cpu() are POSIX shell functions and cannot cross into
    # fish. Delegate to the hermetic entrypoints, which apply the same wrapper
    # (collection provisioning, lazy index build, CUDA->CPU retry) in bash.
    function qmd --description 'project-local qmd (via .agents/bin/qmd)'
        command $TTRPG_ROOT/.agents/bin/qmd $argv
    end

    function qmd-cpu --description 'project-local qmd, GPU forced off'
        command $TTRPG_ROOT/.agents/bin/qmd-cpu $argv
    end
end
