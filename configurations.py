import numpy as np
import itertools
import sys
import h5py


kind = str(sys.argv[1])
N = int(sys.argv[2])


def in_out_attenuator(N):
    """
    Generate all possible in/out state configurations
    of ``N`` attenuator blades.
    """
    return np.asarray(list(itertools.product([np.nan,1], repeat=N)))


def write_h5(config_table):
    """
    Write the configurations set into an HDF5 file.
 
    Parameters:
       config_table : ``NumPy Array``

    """
    h5 = h5py.File('configs.h5', 'w')
    configs = h5.create_dataset('configurations', (len(config_table),N,), dtype='f')
    configs[:] = config_table[:]
    h5.close()


if __name__ == '__main__':
    if kind == 'inout':
        config_table = in_out_attenuator(N)
        write_h5(config_table)
