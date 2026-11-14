# SMA Labirinto (SPADE)

Sistema multiagentes com tres agentes cegos (Labirinto, Pai e Filho) que
resolvem um labirinto usando busca em profundidade (DFS). O Pai explora,
otimiza o caminho (BFS no mapa explorado) e envia ao Filho, que o percorre.

## Agentes e comportamentos
Cada comportamento usa um Template e so recebe mensagens da sua performativa.

- Labirinto
  - ResponderCaminhos: recebe request(caminhos)
  - ResponderMovimento: recebe subscribe(direcao)
  - ResponderObjetivo: recebe query-if(objetivo)
- Pai
  - Resolver: DFS, recebe as respostas do Labirinto
  - AguardarFilho: recebe o inform("Cheguei!") do Filho
- Filho
  - ReceberProposta: recebe propose(string das direcoes)
  - Percorrer: segue o caminho, recebe as respostas do Labirinto

## Instalacao
pip install -r requirements.txt

## Execucao (dois terminais)
1) spade run
2) python labirinto_spade.py

Para ver as mensagens do protocolo:
VERBOSE=1 python labirinto_spade.py

## Arquivos
- labirinto_spade.py: codigo dos tres agentes
- log.txt: saida de uma execucao com as mensagens trocadas
- requirements.txt: dependencias (spade e greenlet)
