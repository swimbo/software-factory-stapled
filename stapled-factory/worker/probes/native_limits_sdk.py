"""Unpaid component stress tests; never a model-facing execution primitive."""
import json,os,pathlib,sys,threading,time,uuid
sys.path.insert(0,'/probe')
from types import SimpleNamespace
from openhands.sdk import Agent,LLM
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.cancellation import CancellationToken
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal import TerminalTool
from openhands.tools.terminal.definition import TerminalAction
from factory_capture_tool import CaptureAction,CaptureExecutor
from bounded_profile import PROFILE,validate_workspace,temporary_option


def main():
    def forbidden(*a,**k):raise AssertionError('inference forbidden')
    LLM.completion=forbidden
    root=pathlib.Path('/workspace');state=ConversationState.create(id=uuid.uuid4(),agent=Agent(llm=LLM(model='anthropic/auto',usage_id='unpaid',api_key='dummy'),tools=[]),workspace=LocalWorkspace(working_dir=str(root)))
    quota=validate_workspace(str(root),PROFILE)
    boundary=json.loads(pathlib.Path('/inputs/boundary.json').read_text())
    assert boundary['HostConfig']['ReadonlyRootfs'] is True
    assert boundary['HostConfig']['Tmpfs']['/tmp']==temporary_option(os.getuid())
    assert set(boundary['HostConfig']['Tmpfs'])=={'/tmp','/workspace'}
    # This disposable stress profile uses ephemeral tmpfs, whereas the factory
    # profile requires an independently prepared, retained workspace bind.
    assert not pathlib.Path('/gateway').exists() and not pathlib.Path('/budget').exists() and not pathlib.Path('/var/run/docker.sock').exists() and not pathlib.Path('/checks/check.py').exists()
    tool=TerminalTool.create(state)[0]
    def run(command,timeout=10):return tool.executor(TerminalAction(command=command,timeout=timeout))
    try:
        obs=run("python3 -B -c \"import time;from pathlib import Path;time.sleep(1);Path('late').write_text('effect')\"",.2)
        time.sleep(1.3);assert (root/'late').read_text()=='effect'
        soft_timeout={'returned_exit_code':obs.exit_code,'effect_after_timeout':True}
        obs=run("python3 -B -c \"print('x'*100000)\"")
        model_bytes=sum(len(c.text.encode()) for c in obs.to_llm_content if hasattr(c,'text'));assert model_bytes<32000
        # Disk exhaustion occurs only on this private64MiB tmpfs.
        code="import errno,os;f=open('fill','wb');limited=False\ntry:\n while True:f.write(b'x'*1048576);f.flush()\nexcept OSError as e:limited=e.errno==errno.ENOSPC\nfinally:f.close();os.unlink('fill')\nassert limited;print('disk limited')"
        import shlex
        obs=run('python3 -B -c '+shlex.quote(code));assert obs.exit_code==0 and 'disk limited' in obs.text
        # Children stay in this fresh cgroup. Reap each before returning a prompt.
        code="import os,signal,time;children=[];limited=False\ntry:\n for _ in range(200):\n  pid=os.fork()\n  if pid==0:time.sleep(20);os._exit(0)\n  children.append(pid)\nexcept OSError:limited=True\nfinally:\n for pid in children:\n  try:os.kill(pid,signal.SIGKILL)\n  except ProcessLookupError:pass\n for pid in children:os.waitpid(pid,0)\nassert limited;print('fork limited')"
        obs=run('python3 -B -c '+shlex.quote(code));assert obs.exit_code==0 and 'fork limited' in obs.text
        # Native cooperative interrupt is distinct from an external namespace kill.
        command="python3 -B -c \"import time;from pathlib import Path;Path('interrupt-ready').write_text('1');time.sleep(10);Path('interrupt-late').write_text('bad')\""
        result=[];thread=threading.Thread(target=lambda:result.append(run(command,20)));thread.start()
        for _ in range(100):
            if (root/'interrupt-ready').exists():break
            time.sleep(.02)
        assert (root/'interrupt-ready').exists();tool.executor.interrupt();thread.join(5);assert not thread.is_alive() and not (root/'interrupt-late').exists()
    finally:tool.executor.close()
    token=CancellationToken();token.cancel();executor=CaptureExecutor('/tmp/cancelled-capture',str(root))
    result=json.loads(executor(CaptureAction(operation='capture',argv=['python3','-c',"from pathlib import Path;Path('must-not-execute').touch()"]),conversation=SimpleNamespace(state=state,cancel_token=token)).text)
    assert result['reason']=='cancelled' and not (root/'must-not-execute').exists()
    token=CancellationToken();timer=threading.Timer(.2,token.cancel);timer.start()
    result=json.loads(executor(CaptureAction(operation='capture',argv=['python3','-c',"import time;print('started',flush=True);time.sleep(10)"]),conversation=SimpleNamespace(state=state,cancel_token=token)).text);timer.join()
    assert result['reason']=='cancelled' and not result['capture_complete'] and 'started' in result['preview']
    result=json.loads(executor(CaptureAction(operation='capture',argv=['python3','-c',"import os\nwhile True:os.write(1,b'x'*65536)"]),conversation=SimpleNamespace(state=state,cancel_token=CancellationToken())).text)
    assert result['reason']=='output_limit' and result['total_bytes']<=1048576 and len(result['preview'].encode())<=4096
    print('FACTORY_LIMIT_PROBE='+json.dumps({'schema':'factory.native-limits-probe/v1','model_requests':0,'workspace_capacity':quota,'production_bind_mount_profile_activated':False,'native_timeout_is_not_termination':soft_timeout,'native_model_output_bytes':model_bytes,'disk_exhaustion_contained':True,'fork_exhaustion_contained':True,'native_cooperative_interrupt':True,'capture_sdk_cancellation_before_and_during':True,'capture_output_flood_contained':True,'gateway_budget_socket_and_checker_absent':True}),flush=True)

main()
raise SystemExit(0)
