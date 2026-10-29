import sys

if __name__ == "__main__":   # (not when the processes that draw the maps start)
    try:
        from .app import main
        main()
    except KeyboardInterrupt:   # Ctrl-C while it starts up
        sys.exit(130)
