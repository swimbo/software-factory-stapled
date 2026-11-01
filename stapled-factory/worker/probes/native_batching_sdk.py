"""Unpaid native TerminalTool characterization through exact AgentServer binary.

Load with --import-modules and exit before serving. No run/completion calls.
"""
import base64,hashlib,json,os,pathlib,shutil,subprocess,time,uuid
from openhands.sdk import LLM,Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal import TerminalTool
from openhands.tools.terminal.definition import TerminalAction
from batching import commands


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args]).decode().strip()


def main():
    def forbidden(*a,**k):raise AssertionError('inference forbidden')
    LLM.completion=forbidden
    tasks=json.loads(pathlib.Path('/inputs/tasks.json').read_text())['tasks'];results=[]
    for task in tasks:
        for mode in ('sequential','batched'):
            root=pathlib.Path('/workspace')/(task['id']+'-'+mode);shutil.copytree('/seed',root)
            git(root,'init','-q');git(root,'config','user.name','Factory Baseline');git(root,'config','user.email','factory@example.invalid')
            git(root,'add','--all')
            subprocess.run(['git','-C',str(root),'commit','-q','-m','Frozen Phase 9d development baseline'],env={**os.environ,'GIT_AUTHOR_DATE':'2026-10-30T00:00:00Z','GIT_COMMITTER_DATE':'2026-10-30T00:00:00Z'},check=True)
            baseline=git(root,'rev-parse','HEAD');assert baseline=='e25eb19801c9a82b5255ecaccc35ce6e0677e91f'
            state=ConversationState.create(id=uuid.uuid4(),agent=Agent(llm=LLM(model='anthropic/auto',usage_id='unpaid',api_key='dummy'),tools=[]),workspace=LocalWorkspace(working_dir=str(root)))
            tool=TerminalTool.create(state)[0];outputs=[];started=time.monotonic()
            try:
                for command in commands(task['id'],mode=='batched'):
                    obs=tool.executor(TerminalAction(command=command,timeout=10))
                    assert obs.exit_code==0,(task['id'],mode,obs.exit_code,obs.text[-1000:])
                    outputs.append(sum(len(x.text.encode()) for x in obs.to_llm_content if hasattr(x,'text')))
            finally:tool.executor.close()
            changed=git(root,'diff','--name-only',baseline,'HEAD').splitlines()
            assert changed and set(changed)<=set(task['allowed_paths'])
            assert not git(root,'status','--porcelain')
            archive=subprocess.check_output(['git','-C',str(root),'archive','HEAD'])
            results.append({'task_id':task['id'],'mode':mode,'terminal_calls':len(outputs),'observation_utf8_bytes':sum(outputs),'elapsed_s':time.monotonic()-started,'git_tree':git(root,'rev-parse','HEAD^{tree}'),'allowed_paths_only':True,'clean':True,'archive_sha256':hashlib.sha256(archive).hexdigest(),'archive_base64':base64.b64encode(archive).decode()})
    for task in tasks:
        group=[r for r in results if r['task_id']==task['id']];assert group[0]['git_tree']==group[1]['git_tree']
    print('FACTORY_BATCH_PROBE='+json.dumps({'schema':'factory.native-batching-probe/v1','model_requests':0,'cases':results}),flush=True)

main()
raise SystemExit(0)
