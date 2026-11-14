"""
SMA com SPADE: Labirinto, Pai e Filho (agentes cegos).

Protocolo (conforme o diagrama de sequencia):
  Pai -> Labirinto : request(caminhos)          Labirinto -> Pai : inform("E D C B")
  Pai -> Labirinto : subscribe(E|D|C|B)         Labirinto -> Pai : inform(ok|nok)
  Pai -> Labirinto : query-if(objetivo)         Labirinto -> Pai : inform-done | failure
  Pai -> Filho     : propose(string das direcoes)
  Filho -> Labirinto : subscribe(E|D|C|B)       Labirinto -> Filho : inform(ok|nok)
  Filho -> Pai     : inform("Cheguei!")

Uso:
  python labirinto_spade.py [arquivo_labirinto.txt]

Formato do arquivo: '#' parede, 'S' inicio, 'G' objetivo, qualquer outro = livre.
Variaveis de ambiente: XMPP_DOMAIN (padrao: localhost), VERBOSE=1 para logar mensagens.
"""
import asyncio
import os
import sys
from collections import deque

import spade
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from spade.template import Template

DOMAIN = os.getenv("XMPP_DOMAIN", "localhost")
VERBOSE = os.getenv("VERBOSE", "0") == "1"
PASSWORD = "senha123"
JID_LAB = f"labirinto@{DOMAIN}"
JID_PAI = f"pai@{DOMAIN}"
JID_FILHO = f"filho@{DOMAIN}"

# E=esquerda, D=direita, C=cima, B=baixo -> (delta linha, delta coluna)
DIRECOES = {"E": (0, -1), "D": (0, 1), "C": (-1, 0), "B": (1, 0)}
OPOSTA = {"E": "D", "D": "E", "C": "B", "B": "C"}

LABIRINTO_PADRAO = """\
###########
#S    #   #
# ### # # #
# #   # # #
# # ### # #
#         #
# ##### # #
#     #   G
###########
"""


def log(quem, texto):
    if VERBOSE:
        print(f"[{quem}] {texto}")


def bare(jid):
    return str(jid).split("/")[0]


def msg(to, perf, body):
    return Message(to=to, body=body, metadata={"performative": perf})


def template(sender=None, *performativas):
    """Template que aceita mensagens de `sender` com qualquer uma das performativas."""
    ts = []
    for p in performativas or [None]:
        t = Template(sender=sender)
        if p:
            t.set_metadata("performative", p)
        ts.append(t)
    r = ts[0]
    for t in ts[1:]:
        r = r | t
    return r


def soma(pos, d):
    return (pos[0] + DIRECOES[d][0], pos[1] + DIRECOES[d][1])


# --------------------------------------------------------------------------
# Agente Labirinto
# --------------------------------------------------------------------------
class Labirinto(Agent):
    def __init__(self, jid, password, texto):
        super().__init__(jid, password)
        linhas = [l for l in texto.splitlines() if l]
        self.grade = linhas
        self.inicio = self.objetivo = None
        for i, l in enumerate(linhas):
            for j, c in enumerate(l):
                if c == "S":
                    self.inicio = (i, j)
                elif c == "G":
                    self.objetivo = (i, j)
        if self.inicio is None or self.objetivo is None:
            raise ValueError("Labirinto precisa de 'S' e 'G'")
        self.posicoes = {}  # posicao de cada agente resolvedor (por JID)

    def livre(self, p):
        i, j = p
        return (0 <= i < len(self.grade) and 0 <= j < len(self.grade[i])
                and self.grade[i][j] != "#")

    def posicao_de(self, quem):
        return self.posicoes.setdefault(quem, self.inicio)

    # Um comportamento para cada performativa; cada um so recebe a sua.
    class ResponderCaminhos(CyclicBehaviour):          # request(caminhos)
        async def run(self):
            m = await self.receive(timeout=5)
            if m is None:
                return
            ag, quem = self.agent, bare(m.sender)
            log("Labirinto", f"<- request({m.body}) de {quem.split('@')[0]}")
            pos = ag.posicao_de(quem)
            dirs = [d for d in "EDCB" if ag.livre(soma(pos, d))]
            await self.send(msg(quem, "inform", " ".join(dirs)))

    class ResponderMovimento(CyclicBehaviour):         # subscribe(direcao)
        async def run(self):
            m = await self.receive(timeout=5)
            if m is None:
                return
            ag, quem = self.agent, bare(m.sender)
            log("Labirinto", f"<- subscribe({m.body}) de {quem.split('@')[0]}")
            pos, d = ag.posicao_de(quem), m.body.strip()
            if d in DIRECOES and ag.livre(soma(pos, d)):
                ag.posicoes[quem] = soma(pos, d)
                await self.send(msg(quem, "inform", "ok"))
            else:
                await self.send(msg(quem, "inform", "nok"))

    class ResponderObjetivo(CyclicBehaviour):          # query-if(objetivo)
        async def run(self):
            m = await self.receive(timeout=5)
            if m is None:
                return
            ag, quem = self.agent, bare(m.sender)
            log("Labirinto", f"<- query-if({m.body}) de {quem.split('@')[0]}")
            perf = "inform-done" if ag.posicao_de(quem) == ag.objetivo else "failure"
            await self.send(msg(quem, perf, "objetivo"))

    async def setup(self):
        self.add_behaviour(self.ResponderCaminhos(), template(None, "request"))
        self.add_behaviour(self.ResponderMovimento(), template(None, "subscribe"))
        self.add_behaviour(self.ResponderObjetivo(), template(None, "query-if"))


# --------------------------------------------------------------------------
# Agente Pai: DFS (cego) + otimizacao do caminho
# --------------------------------------------------------------------------
class Pai(Agent):
    def __init__(self, jid, password, jid_filho):
        super().__init__(jid, password)
        self.jid_filho = jid_filho
        self.fim = asyncio.Event()
        self.resultado = None

    class Resolver(OneShotBehaviour):
        async def perguntar(self, perf, body):
            await self.send(msg(JID_LAB, perf, body))
            r = await self.receive(timeout=10)
            if r is None:
                raise TimeoutError(f"Labirinto nao respondeu a {perf}({body})")
            log("Pai", f"{perf}({body}) -> {r.get_metadata('performative')}({r.body})")
            return r

        async def caminhos(self):
            r = await self.perguntar("request", "caminhos")
            return r.body.split()

        async def mover(self, d):
            r = await self.perguntar("subscribe", d)
            return r.body == "ok"

        async def eh_objetivo(self):
            r = await self.perguntar("query-if", "objetivo")
            return r.get_metadata("performative") == "inform-done"

        async def busca_profundidade(self):
            """DFS iterativa. O Pai nao ve nada: usa coordenadas relativas
            (dead reckoning) e as respostas do Labirinto.
            Retorna (caminho_da_pilha, grafo_explorado, objetivo)."""
            pos = (0, 0)
            visitados = {pos}
            grafo = {pos: set()}          # arestas conhecidas: pos -> {vizinhos}
            pendentes = {pos: await self.caminhos()}
            pilha = []                    # direcoes tomadas ate a posicao atual

            while True:
                if not pendentes[pos]:    # beco/esgotado: backtracking
                    if not pilha:
                        return None, grafo, None
                    d = pilha.pop()
                    await self.mover(OPOSTA[d])
                    pos = soma(pos, OPOSTA[d])
                    continue

                d = pendentes[pos].pop(0)
                nova = soma(pos, d)
                grafo.setdefault(nova, set())
                grafo[pos].add(nova)
                grafo[nova].add(pos)
                if nova in visitados:
                    continue

                if not await self.mover(d):
                    continue
                visitados.add(nova)
                pilha.append(d)
                pos = nova

                if await self.eh_objetivo():
                    return list(pilha), grafo, pos
                pendentes[pos] = await self.caminhos()

        @staticmethod
        def otimizar(grafo, objetivo):
            """BFS no grafo explorado -> caminho minimo conhecido."""
            origem = (0, 0)
            ant = {origem: None}
            fila = deque([origem])
            while fila:
                p = fila.popleft()
                if p == objetivo:
                    break
                for d in "EDCB":
                    q = soma(p, d)
                    if q in grafo[p] and q not in ant:
                        ant[q] = (p, d)
                        fila.append(q)
            caminho = []
            p = objetivo
            while ant[p] is not None:
                p, d = ant[p]
                caminho.append(d)
            return caminho[::-1]

        async def run(self):
            try:
                pilha, grafo, objetivo = await self.busca_profundidade()
                if pilha is None:
                    print("[Pai] Labirinto sem saida.")
                    self.agent.fim.set()
                    return
                print(f"[Pai] DFS achou a saida. Caminho da DFS ({len(pilha)}): "
                      f"{''.join(pilha)}")
                otimo = self.otimizar(grafo, objetivo)
                print(f"[Pai] Caminho otimizado ({len(otimo)}): {''.join(otimo)}")
                self.agent.resultado = "".join(otimo)

                await self.send(msg(self.agent.jid_filho, "propose", self.agent.resultado))
            except Exception:
                self.agent.fim.set()
                raise

    class AguardarFilho(OneShotBehaviour):             # inform("Cheguei!") do Filho
        async def run(self):
            r = await self.receive(timeout=60)
            if r is not None and r.get_metadata("performative") == "inform":
                print(f"[Pai] Filho respondeu: {r.body}")
            else:
                print("[Pai] Filho nao chegou.")
            self.agent.fim.set()

    async def setup(self):
        # Resolver so recebe respostas do Labirinto; AguardarFilho so recebe do Filho.
        self.add_behaviour(
            self.Resolver(),
            template(JID_LAB, "inform", "inform-done", "failure"))
        self.add_behaviour(self.AguardarFilho(), template(JID_FILHO))


# --------------------------------------------------------------------------
# Agente Filho: segue o caminho recebido
# --------------------------------------------------------------------------
class Filho(Agent):
    class ReceberProposta(CyclicBehaviour):            # propose(string das direcoes)
        async def run(self):
            m = await self.receive(timeout=5)
            if m is None:
                return
            log("Filho", f"<- propose({m.body})")
            # cria o comportamento que percorre o caminho; ele so recebe
            # as respostas (inform ok|nok) do Labirinto
            self.agent.add_behaviour(
                self.agent.Percorrer(m.body, bare(m.sender)),
                template(JID_LAB, "inform"))

    class Percorrer(OneShotBehaviour):
        def __init__(self, caminho, pai):
            super().__init__()
            self.caminho, self.pai = caminho, pai

        async def run(self):
            for d in self.caminho:
                await self.send(msg(JID_LAB, "subscribe", d))
                r = await self.receive(timeout=10)
                if r is None or r.body != "ok":
                    await self.send(msg(self.pai, "failure", f"parei em {d}"))
                    return
            await self.send(msg(self.pai, "inform", "Cheguei!"))

    async def setup(self):
        self.add_behaviour(self.ReceberProposta(), template(JID_PAI, "propose"))


# --------------------------------------------------------------------------
async def main(texto):
    lab = Labirinto(JID_LAB, PASSWORD, texto)
    filho = Filho(JID_FILHO, PASSWORD)
    pai = Pai(JID_PAI, PASSWORD, JID_FILHO)
    for a in (lab, filho, pai):  # pai por ultimo: ja encontra os outros online
        await a.start(auto_register=True)
    await pai.fim.wait()
    await asyncio.sleep(0.5)
    for a in (pai, filho, lab):
        await a.stop()
    print(f"[Labirinto] posicao final do Filho: {lab.posicoes.get(JID_FILHO)} "
          f"(objetivo: {lab.objetivo})")


if __name__ == "__main__":
    texto = open(sys.argv[1]).read() if len(sys.argv) > 1 else LABIRINTO_PADRAO
    spade.run(main(texto))
