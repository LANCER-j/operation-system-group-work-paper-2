import sys, hashlib, json, difflib
from pathlib import Path
source=Path(sys.argv[1]); logs=Path(sys.argv[2]);logs.mkdir(parents=True,exist_ok=True)
p=source/'kernel/sched/sched_ktz.c'
before=p.read_text()
old='\tstruct sched_domain *curr = rcu_dereference(per_cpu(sd_llc, cpu));\n\twhile (curr->parent) {'
new='\tstruct sched_domain *curr = rcu_dereference(per_cpu(sd_llc, cpu));\n\t/* Some virtual topologies have no shared-cache domain. */\n\tif (!curr)\n\t\tcurr = rcu_dereference(cpu_rq(cpu)->sd);\n\twhile (curr && curr->parent) {'
old2='\troot_domain = get_top_domain(this_cpu);\n\tthis_tdq = TDQ(cpu_rq(this_cpu));'
new2='\troot_domain = get_top_domain(this_cpu);\n\tif (!root_domain)\n\t\treturn curr_cpu; /* The core validates affinity and CPU availability. */\n\tthis_tdq = TDQ(cpu_rq(this_cpu));'
def blob(s):
 b=s.encode();return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
if old in before and old2 in before:
 assert blob(before)=='9e9d7a65d184df9edbc969f140460933454a68bd','Unexpected ULE source before compatibility fix'
 after=before.replace(old,new,1).replace(old2,new2,1)
 (logs/'ule-llc-null.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/kernel/sched/sched_ktz.c',tofile='b/kernel/sched/sched_ktz.c')))
 p.write_text(after)
elif new in before and new2 in before:
 after=before
else:raise SystemExit('Unexpected ULE source; refusing to guess compatibility changes')
(logs/'ule-compatibility.json').write_text(json.dumps({'file':'kernel/sched/sched_ktz.c','original_git_blob':'9e9d7a65d184df9edbc969f140460933454a68bd','modified_git_blob':blob(after),'reason':'Observed boot NULL dereference when sd_llc is absent under QEMU WHPX; fall back to rq->sd and guard absent domains','scope':'Virtual-topology compatibility. Normal non-NULL LLC path retained. This is a deviation from author HEAD.'},indent=2))
print('ULE virtual-topology compatibility patch verified')
