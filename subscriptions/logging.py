import logging
from functools import partial, partialmethod

# add a high log level (higher than ERROR) which will store
# information helpful for backtracing
logging.ARCHIVE = logging.ERROR + 1
logging.addLevelName(logging.ARCHIVE, 'ARCHIVE')
logging.Logger.archive = partialmethod(logging.Logger.log, logging.ARCHIVE)
logging.archive = partial(logging.log, logging.ARCHIVE)
