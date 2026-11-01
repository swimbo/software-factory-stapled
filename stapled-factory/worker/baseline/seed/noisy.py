"""Generate bounded, deterministic discovery noise."""
for index in range(10000):
    print('record', index, 'target' if index == 6789 else 'ordinary')
