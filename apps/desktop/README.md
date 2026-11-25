# SpectralIndex Desktop

Instalador offline para Windows 10/11. O aplicativo usa Docker Desktop para executar a API, os trabalhadores, PostgreSQL, Redis e MinIO localmente. A edição leve abre em janela própria com WebView2. Não exige login ou internet durante o uso após a instalação dos pré-requisitos.

## Por que o Docker Desktop é necessário

O programa desktop reaproveita o mecanismo científico do site, que depende de uma API Python, trabalhadores de processamento, banco PostgreSQL, fila Redis e armazenamento MinIO. O WebView2 abre a interface; o Docker executa esses serviços no próprio computador. As imagens estão incluídas no instalador para uso offline. Uma edição sem Docker exigiria substituir ou empacotar esses serviços como processos nativos do Windows e migrar os dados locais, por isso não é apenas uma mudança no instalador.

## Instalação

1. Instale e inicie o Docker Desktop.
2. Execute `SpectralIndex-Desktop-Setup-0.7.0.exe` e abra **SpectralIndex Desktop (leve)** pelo menu Iniciar.
3. Aguarde a importação das imagens Docker na primeira execução. Os dados são mantidos em volumes locais.

## Calibração de várias repetições

1. Importe os TIFFs das cinco bandas de cada captura no mesmo dataset. O JPG RGB pode acompanhar os arquivos como referência visual.
2. Em **Calibração**, escolha a primeira captura do painel e, se houver, marque outras capturas de referência. Selecione as demais capturas a calibrar.
3. Na etapa **Selecionar ROIs**, escolha cada referência no seletor e desenhe a ROI da mesma superfície do painel na posição correta daquela imagem. As coordenadas de uma captura não são copiadas para outra.
4. Informe a reflectância da superfície para as cinco bandas e execute. O desktop calcula um coeficiente por banda em cada referência e aplica a média aritmética dos coeficientes a todas as capturas selecionadas, inclusive às referências. Com uma referência, aplica o coeficiente dessa captura.
5. Confira as prévias por captura e o RMSE no painel após a combinação. O pacote exportado inclui TIFF Float32 com o nome original da imagem, coeficientes individuais e aplicados, além do JSON de auditoria.

O RMSE calculado nas referências não é uma validação independente. Sem certificado individual do painel e verificação das condições de iluminação, trate as saídas como estimativas de reflectância.

## Remover imagens adicionadas

Na seção **Upload de imagens**, cada imagem validada mostra **Remover**. A ação a retira das capturas ativas e dos próximos processamentos. Use **Exibir removidas** e **Restaurar** para desfazer. Os originais e os resultados de calibrações anteriores são preservados para auditoria; a remoção não libera espaço em disco. Um upload em andamento deve terminar ou ser cancelado antes de remover seus itens.

## Calibração antes dos índices

O fluxo principal do desktop segue **Upload → Bandas → Calibração → ROI → Índices → Processar → Resultados**. Na etapa de calibração, baixe o ZIP com os TIFFs Float32 e a auditoria. Na configuração dos índices, escolha uma calibração concluída em **Imagens usadas para calcular os índices**; o programa confere se todas as capturas e bandas necessárias estão presentes. Calibrações novas guardam cada TIFF separadamente para leitura direta pelo processador. Calibrações anteriores continuam legíveis pelo ZIP. Em Resultados, **Baixar imagens calibradas** gera somente os TIFFs e **Baixar pacote completo** inclui os TIFFs calibrados e os índices do job.

O instalador 0.7.0 usa uma janela nativa do Windows com WebView2, mantendo a mesma API e os trabalhadores locais. Ele inclui as imagens Docker offline e mede 396,23 MB (396.227.111 bytes). O Docker Desktop continua necessário no computador de destino. O Windows 11 já inclui o WebView2 Runtime; em computadores Windows 10 sem esse componente, instale o [WebView2 Evergreen Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) antes de iniciar o aplicativo. A edição leve usa o mesmo perfil de dados da edição anterior: feche a anterior antes de abrir a nova.

## Gerar o instalador

Na raiz do repositório, execute:

```powershell
powershell -ExecutionPolicy Bypass -File apps/desktop/build-installer.ps1
```

O comando compila o frontend, constrói a imagem compartilhada, empacota as imagens base e gera `dist/desktop/SpectralIndex-Desktop-Setup-0.6.0.exe`. Use `-SkipImages` somente para alterações de interface quando o arquivo `apps/desktop/offline-images.tar.gz` já estiver atualizado.

Para gerar a edição leve em janela própria, com o arquivo de imagens offline já preparado:

```powershell
powershell -ExecutionPolicy Bypass -File apps/desktop/native/build-installer.ps1
```

O resultado é `dist/desktop/SpectralIndex-Desktop-Setup-0.7.0.exe`. O script obtém o SDK WebView2 do NuGet oficial, compila a janela nativa com o compilador .NET Framework do Windows e usa o NSIS para o instalador. A instalação coloca a edição leve em uma pasta separada e não remove a edição anterior nem os dados locais.
