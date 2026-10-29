import sys

try:
    from .app import main
    main()
except KeyboardInterrupt:   # Ctrl-C while it starts up
    sys.exit(130)
