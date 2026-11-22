import sys, os, subprocess, time, json
from pathlib import Path
base=Path(__file__).resolve().parent
sys.path.insert(0,str(base/'experiment'))
from trace_cpu import TraceCpuRecorder
out=base/'calibration'/'trace-4.15-not-a-paper-result'
rec=TraceCpuRecorder(out,0,interval=.1)
rec.start()
origin=time.monotonic()
try:
 p=subprocess.Popen([str(base/'calibration'/'fibo'),'40','10'],stdout=subprocess.PIPE,universal_newlines=True,preexec_fn=lambda:os.sched_setaffinity(0,{0}))
 rec.attach('fibo',p.pid)
 text=p.communicate()[0]
 assert p.returncode==0
 end=time.monotonic()
 row=rec.finish(origin,end)
 result={'purpose':'trace validation on build kernel, not paper result','process':json.loads(text),'trace':row}
 (out/'comparison.json').write_text(json.dumps(result,indent=2))
 print(json.dumps(result))
finally:rec.close()
