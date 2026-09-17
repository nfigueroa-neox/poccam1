/**
 * Despliegue a producción del backend nube.
 *
 * ── Por qué existe este script ──────────────────────────────────────
 *
 * El proyecto en Vercel tiene **Root Directory = `backend-nube`**, porque
 * en su momento se desplegó desde la raíz del monorepo (y desplegar desde
 * la raíz sin ese ajuste publica una versión sin `api/`, que responde 404).
 *
 * Eso significa que el `vercel` DEBE ejecutarse desde la RAÍZ del monorepo:
 * Vercel le añade el Root Directory por su cuenta. Si se ejecuta desde
 * `backend-nube/`, busca `backend-nube/backend-nube` y falla con:
 *
 *     Error: The specified Root Directory "backend-nube" does not exist.
 *
 * Como el `.vercel/` (el vínculo al proyecto) vive en `backend-nube/`, al
 * correr desde la raíz hay que pasar el proyecto y el scope explícitamente.
 *
 * Uso:
 *   node deploy.mjs whoami     # verifica la sesión
 *   node deploy.mjs deploy     # despliega a producción
 *   node deploy.mjs dev        # entorno de desarrollo local
 */
import { readFileSync, existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const aqui = dirname(fileURLToPath(import.meta.url)); // .../backend-nube
const raizMonorepo = resolve(aqui, '..');

// ── Verificar que estamos en el monorepo correcto ───────────────────

if (!existsSync(join(raizMonorepo, 'worker')) ||
    !existsSync(join(raizMonorepo, 'concentrador'))) {
  console.error(
    `\n❌ ERROR: no se encontró el monorepo en ${raizMonorepo}\n` +
      '   (faltan las carpetas `worker/` y `concentrador/`).\n',
  );
  process.exit(1);
}

const rutaToken = join(aqui, '.vercel-token');
if (!existsSync(rutaToken)) {
  console.error(
    `\n❌ ERROR: no se encontró ${rutaToken}\n\n` +
      '   Crear el token en https://vercel.com/account/tokens y guardarlo\n' +
      '   en ese archivo (está gitignored).\n',
  );
  process.exit(1);
}

// ── Identidad del proyecto ──────────────────────────────────────────
// Se lee de backend-nube/.vercel/project.json (creado por `vercel link`).

const rutaProyecto = join(aqui, '.vercel', 'project.json');
if (!existsSync(rutaProyecto)) {
  console.error(
    `\n❌ ERROR: no se encontró ${rutaProyecto}\n\n` +
      '   Vincular el proyecto una vez:\n' +
      '       cd backend-nube && npx vercel link\n',
  );
  process.exit(1);
}
const proyecto = JSON.parse(readFileSync(rutaProyecto, 'utf8'));

const token = readFileSync(rutaToken, 'utf8').trim();
const accion = process.argv[2] || 'whoami';

// Flags que identifican el proyecto al ejecutar desde la raíz del monorepo.
const identidad = [
  '--project', proyecto.projectName,
  '--scope', proyecto.orgId,
  '--token', token,
];

const comandos = {
  whoami: ['npx', ['vercel', 'whoami', '--token', token]],
  deploy: ['npx', ['vercel', '--prod', '--yes', ...identidad]],
  dev: ['npx', ['vercel', 'dev', ...identidad]],
};

const [cmd, args] = comandos[accion] ?? comandos.whoami;
console.log(`Ejecutando: vercel ${args.filter((a) => a !== token).join(' ')}`);
console.log(`Directorio: ${raizMonorepo}  (Root Directory del proyecto en Vercel)\n`);

try {
  execFileSync(cmd, args, {
    stdio: 'inherit',
    shell: true,
    cwd: raizMonorepo,
  });
} catch {
  // El error ya se mostró con stdio: inherit; solo propagar el fallo.
  process.exit(1);
}
