import { copyFile, lstat, mkdir, readlink, symlink } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const SOURCE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

async function linkMatches(link: string, target: string): Promise<boolean> {
  try {
    const stat = await lstat(link)
    if (!stat.isSymbolicLink()) return false
    return resolve(dirname(link), await readlink(link)) === resolve(target)
  } catch {
    return false
  }
}

export async function provisionMaidAiProfile(dshHome: string): Promise<string> {
  const profileDir = join(dshHome, 'profiles', 'maidai')
  await mkdir(profileDir, { recursive: true })
  await copyFile(join(SOURCE_ROOT, 'profile', 'package.json'), join(profileDir, 'package.json'))
  await copyFile(join(SOURCE_ROOT, 'profile', 'cordis.patch.yml'), join(profileDir, 'cordis.patch.yml'))
  await copyFile(join(SOURCE_ROOT, 'profile', 'pnpm-workspace.yaml'), join(profileDir, 'pnpm-workspace.yaml'))
  const scopeDir = join(profileDir, 'node_modules', '@maidai')
  const driverLink = join(scopeDir, 'dsh-driver')
  await mkdir(scopeDir, { recursive: true })
  try {
    await lstat(driverLink)
    if (!await linkMatches(driverLink, SOURCE_ROOT)) {
      throw new Error(`managed profile package path is occupied: ${driverLink}`)
    }
  } catch (error: unknown) {
    if (error instanceof Error && 'code' in error && error.code === 'ENOENT') {
      await symlink(SOURCE_ROOT, driverLink, process.platform === 'win32' ? 'junction' : 'dir')
    } else if (error instanceof Error && error.message.startsWith('managed profile')) {
      throw error
    }
  }
  return profileDir
}
