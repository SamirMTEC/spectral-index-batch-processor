# Calibração Radiométrica Multiespectral

## Estado e dados de referência

O módulo usa as cinco bandas de capturas DJI Phantom 4 Multispectral já importadas no SpectralIndex. Uma sessão em lote pode usar uma ou mais capturas do painel para calibrar várias capturas do mesmo dataset. Cada captura de referência tem suas próprias ROIs e seus próprios cinco coeficientes. A aplicação armazena cada TIFF original sem modificá-lo. O processamento cria arquivos novos e registra os hashes dos originais no histórico.

Ainda não há certificado individual do painel MAPIR do usuário. O botão **Preencher perfil MAPIR da especificação** insere os valores informados no documento de 16/11/2026, com procedência declarada e sem alegação de certificação individual. Os testes com reflectâncias sintéticas identificam expressamente esses números como **SYNTHETIC TEST ONLY**; eles testam o software e **não** validam fisicamente a reflectância. Também é possível editar os valores ou importar um CSV com colunas `surface,band,reflectance` ou `surface,BLUE,GREEN,RED,RED_EDGE,NIR`. Marque como certificado somente ao importar o arquivo correspondente ao painel individual.

## Fluxo

1. Abra um dataset e escolha **Calibração Radiométrica**. Importe os TIFFs e escolha **Painel + lote de imagens**. Selecione a primeira captura do painel e, se houver, outras capturas de referência. Marque as capturas que deseja calibrar e escolha explicitamente qual referência será aplicada a cada uma. As capturas de referência também são exportadas calibradas. Informe experimento, data, fuso e condições de luz.
2. Confira a banda e os metadados reconhecidos. O visualizador permite alternar bandas, inspecionar DN e desenhar ROIs retangulares ou poligonais. Brilho e contraste afetam apenas a prévia.
3. Escolha **um** dos quatro alvos MAPIR. Alterne entre as capturas de referência e desenhe a ROI desse alvo em cada painel. Revise as cinco bandas; as outras três superfícies podem ser marcadas para checagem interna. Se a posição divergir entre sensores, crie ROIs individuais por banda. A propagação automática exige os deslocamentos ópticos DJI e captura praticamente parada. O branco gera o alerta não bloqueante previsto na especificação.
4. Cadastre as cinco reflectâncias do alvo, manualmente, pelo perfil MAPIR ou por CSV. No lote, o método é proporcional e a medida de entrada é o sinal DJI corrigido. O DN bruto permanece apenas no fluxo de uma captura e não é transferido automaticamente para outras imagens.
5. Execute. Um fator `K` por banda é calculado **separadamente em cada referência**. Cada captura de destino recebe correção instrumental com **seus próprios metadados** antes da aplicação do fator da referência escolhida. A tela mostra progresso por captura, associação à referência, erros e opção de cancelar ou retomar capturas não concluídas.
6. Baixe o ZIP. Cada pasta `captures/<id>/` contém cinco TIFFs Float32, cinco máscaras `quality-*.tif`, `QA.csv` e `calibration.json`. Referências adicionais ficam em `references/<id>/`. O ZIP também inclui `audit.json`, `roi-statistics.csv`, `coefficients.csv`, `reference-coefficients.csv`, prévias do primeiro painel e relatório PDF. O JSON de cada destino e as tags TIFF registram o identificador da referência aplicada.

## Equações e interpretação

No lote, `K(r,b) = ρ_ref(b) / mediana[S_painel(r,b)]` e `ρ_estimada(i,b,x,y) = K(referência_de_i,b) × S(i,b,x,y)`. `S` é o sinal corrigido dos metadados DJI específicos da captura: nível de preto, vinhetagem, ganho e exposição. **Esse sinal não representa radiância absoluta.** Outras superfícies do mesmo painel fornecem checagem interna, não validação física independente. O resultado identifica separadamente cálculo matemático, checagem interna, validação radiométrica e comparação com Agisoft.

Os TIFFs Float32 são gravados em blocos, com `NaN` para pixels inválidos. Estimativas fora de `[0,1]` são preservadas e identificadas na máscara de qualidade (bit 1: inválido; bit 2: saturado; bit 4: fora da faixa). A saída mantém a georreferência presente no original. Os TIFFs DJI fornecidos não têm geotransformação, portanto seus produtos não ficam georreferenciados apenas por possuir extensão TIFF. Os metadados completos de entrada ficam no manifesto JSON.

## Limites atuais

- O lote usa fatores proporcionais calculados em uma ou mais fotografias do painel. A associação entre destino e referência é manual; o sistema não interpola coeficientes entre painéis nem mede a estabilidade da iluminação automaticamente. Os modos B (irradiância calibrada) e C (painel inicial/final com interpolação) dependem de validação específica e não são aplicados.
- Cada captura usa cinco TIFFs individuais, de uma banda por arquivo. A importação de um TIFF multibanda com mapeamento manual ainda não está disponível.
- A importação de CSV aceita reflectância por banda; a integração de uma curva espectral com uma resposta espectral medida existe na biblioteca científica, mas ainda não está exposta na interface.
- A classificação é operacional e depende de limites configurados pelo usuário. Sem limites, aparece “não avaliável”. Referências não certificadas, DN empírico e ausência de validação independente recebem ressalvas; aprovação operacional não substitui validação metrológica.
- O painel fotografado em um instante não garante a mesma iluminação ao longo do lote. A correção por exposição e ganho é individual; mudanças de irradiância sem referência validada podem comprometer a transferência. O alinhamento geométrico entre bandas é uma etapa separada, necessária antes de índices pixel a pixel.

## Arquivos principais

- `packages/science/src/spectral_engine/calibration.py`: metadados, sinal de câmera, estatísticas e regressão.
- `apps/api/src/spectral_api/routes/calibration.py`: painéis, capturas, execução, histórico e download.
- `apps/worker/src/spectral_worker/calibration.py`: leitura em blocos, ROIs, ajuste, exportação e auditoria.
- `apps/web/src/components/calibration-page.tsx`: fluxo interativo.
- `tests/science/test_calibration.py` e `tests/worker/test_calibration.py`: cálculos sintéticos e leitura dos TIFFs DJI fornecidos.
