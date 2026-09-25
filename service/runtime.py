"""Read-only process/cgroup measurements without secrets or host identifiers."""
from pathlib import Path
def snapshot():
    result={'process_rss_bytes':None,'process_peak_rss_bytes':None,
            'cgroup_memory_bytes':None,'cgroup_memory_limit_bytes':None}
    try:
        fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines() if ':' in line}
        for field,key in [('VmRSS','process_rss_bytes'),('VmHWM','process_peak_rss_bytes')]:
            result[key]=int(fields[field].split()[0])*1024
    except (OSError,KeyError,ValueError): pass
    for key,paths in [('cgroup_memory_bytes',['/sys/fs/cgroup/memory.current','/sys/fs/cgroup/memory/memory.usage_in_bytes']),
                      ('cgroup_memory_limit_bytes',['/sys/fs/cgroup/memory.max','/sys/fs/cgroup/memory/memory.limit_in_bytes'])]:
        for path in paths:
            try:
                value=Path(path).read_text().strip()
                result[key]=int(value) if value!='max' else None
                break
            except (OSError,ValueError): pass
    return result
