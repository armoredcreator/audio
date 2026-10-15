# Decisão de integração

**Status: separação UVR/MDX-Net validada em cinco exemplos pelo usuário; ArmoredSpeech automático NÃO aprovado para produção.**

O usuário relatou que o teste local em CPU terminou com código zero e hashes dos cinco MP4s inalterados. Na escuta relatada, a narração do clipe 1046 permaneceu, a música foi separada da narração em 12025, e os casos 291, 12008 e 178482 foram considerados corretos pelo usuário.

Isso é evidência limitada a esses exemplos e não valida por si só:
- a classificação automática de fala PT-BR, música, ruído, silêncio e fala estrangeira;
- a qualidade de todos os WAVs em uma coleção histórica;
- tempo/RAM para o lote completo;
- integração com a base SQLite e contratos reais de ArmoredCreator/Studio.

## Condições para portar à produção

- implementar e validar o classificador automático, com comportamento fail-closed;
- repetir teste automatizado e auditivo em amostras representativas;
- verificar hashes e existência/legibilidade dos WAVs;
- testar retomada, falhas por item e processamento por fonte;
- revisar integração com o schema SQLite real, sem alterar a base congelada nem as cópias de referência;
- provar que o Studio mantém o WAV em falha e só o remove após confirmar que não precisa mais dele;
- manter RVC, renderização e publicação fora do laboratório até aprovação explícita.
